"""Submit-then-poll support for providers whose 2xx acknowledges a run.

Split out of ``providers.py``: a vendor that answers a submission with a run
handle instead of results needs a whole polling lifecycle -- deadline, retry,
run-state predicates, and a failure taxonomy -- and that lifecycle is a
separate concern from issuing one synchronous request. Keeping it here lets
``providers.py`` stay about adapters.

The mixin is deliberately free of vendor specifics. An adapter opts in by
declaring ``ASYNC_SUBMISSION_OPERATIONS`` and ``ASYNC_POLL_PATHS`` and
overriding the two run-state predicates; both default to the safe answer, so an
adapter that declares poll paths but forgets them degrades to the pending
receipt rather than fetching results of a run that may not have started.
"""

from __future__ import annotations

import re
import socket
import urllib.error
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import quote as urlquote

if TYPE_CHECKING:
    from .providers import HttpResponse, ProviderRequest, ProviderResult


# Vendor run handles are interpolated into an authenticated URL path, so they
# are held to a conservative charset before they get near one.
# At least one alphanumeric is required: `.` and `..` are unreserved, so a
# charset check alone accepts them and `urlquote(..., safe="")` leaves them
# intact, yielding `/v1beta/findall/runs/..` for the vendor's router to
# normalise into a different endpoint.
_ASYNC_RUN_HANDLE_RE = re.compile(r"(?=[A-Za-z0-9._~-]{1,128}$)[.~_-]*[A-Za-z0-9][A-Za-z0-9._~-]*")


@dataclass(frozen=True)
class FetchOutcome:
    """One GET's outcome: the response, or WHY it failed and whether to retry.

    A bare ``None`` return collapsed a 404 (the run is gone; another attempt
    changes nothing), a 429 (back off and retry), a 502 and a socket timeout
    into one indistinguishable failure. The poll loop needs both halves of
    that: the real error class, so the cause reaches the call record instead of
    being erased, and ``retryable``, so a transient hiccup does not discard a
    run that has already cost minutes of polling.
    """

    response: HttpResponse | None = None
    error_class: str | None = None
    retryable: bool = False


class AsyncRunPollingMixin:
    """Poll an accepted async submission to completion.

    Mixed into ``HttpProvider``; every attribute it touches beyond the knobs
    below (``_transport``, ``_base_url``, ``_credential_resolver``,
    ``_headers``, ``_result``, ``_extract_items``, ``_normalize_item``,
    ``_cost_metadata``) is part of that class's own surface.
    """

    # Operations whose 2xx response acknowledges a submitted run instead of
    # returning results. These terminate in their own state; see
    # ``_async_submission_result``.
    ASYNC_SUBMISSION_OPERATIONS: frozenset[str] = frozenset()
    # Candidate keys the acknowledgement may carry the run identifier under,
    # in preference order. Guessing a single key and silently yielding nothing
    # would lose the handle and make the cell unrecoverable by a later poll.
    ASYNC_RUN_HANDLE_KEYS: tuple[str, ...] = ()
    # operation -> (status path template, result path template), each taking a
    # single ``{handle}`` field. An operation absent here has no poll seam and
    # terminates on the acknowledgement as before.
    ASYNC_POLL_PATHS: Mapping[str, tuple[str, str]] = MappingProxyType({})
    ASYNC_POLL_MAX_ATTEMPTS: int = 30
    ASYNC_POLL_INTERVAL_SECONDS: float = 5.0
    # Sized so the attempt cap above is reachable rather than dead: 30 attempts
    # at a 5s cadence is 150s of sleeps, and the rest is headroom for the GETs
    # themselves. Reconciling these two bounds is deliberate -- when they
    # disagreed, whichever was smaller silently became the real limit.
    ASYNC_POLL_BUDGET_SECONDS: float = 300.0
    # A minutes-long poll will meet transient failures; one 502 on one GET is
    # not the run failing. The final result fetch is the worst place to be
    # brittle -- it is a single large-payload download at the end of a run that
    # already cost minutes -- so it gets its own bounded retry with backoff.
    # Both are still bounded by the same wall-clock deadline as the poll
    # itself, so retrying cannot extend the operator's budget.
    ASYNC_RESULT_MAX_ATTEMPTS: int = 4
    ASYNC_RETRY_BACKOFF_SECONDS: float = 1.0
    ASYNC_RETRY_BACKOFF_MAX_SECONDS: float = 15.0

    def resolve_async_run(
        self,
        submission: ProviderResult,
        *,
        request: ProviderRequest,
        sleep: Any = None,
        monotonic: Any = None,
    ) -> ProviderResult:
        """Poll an accepted async run to completion and return its real result.

        Without this seam every FindAll cell terminates ``not_applicable`` with
        zero sources, so two of Parallel's four domains would have no
        comparison column and a downstream bucketing on ``status != ok`` would
        book them as Parallel errors.

        Bounded by WALL CLOCK, not just by attempt count. `timeout_seconds` was
        historically both the per-request and the per-call budget because
        `call` issued exactly one request; a poll loop turns one logical call
        into many, so an attempt-only bound silently multiplies the operator's
        budget (30 attempts x 5s sleeps x a 60s per-GET timeout is ~33 minutes
        inside a single cell, with nothing between cells to interrupt it). The
        deadline below is derived from the caller's own budget, each GET gets a
        fraction of it rather than the whole thing, and an overrun returns a
        distinct error class so it is visible rather than indistinguishable
        from a still-running job.
        """
        # Deferred: `providers` imports this module at its top, so this
        # direction can only be taken once that module is fully loaded.
        from .providers import CredentialUnavailable, make_source, stable_call_id, utc_now

        import time as _time

        sleep = sleep or _time.sleep
        monotonic = monotonic or _time.monotonic
        paths = self.ASYNC_POLL_PATHS.get(request.operation)
        handle = None
        if isinstance(submission.call_record, dict):
            response = submission.call_record.get("response")
            if isinstance(response, Mapping):
                handle = response.get("async_run_handle")
        if not paths or not handle:
            return submission
        if not _ASYNC_RUN_HANDLE_RE.fullmatch(handle):
            # The handle is vendor-controlled data interpolated into an
            # authenticated URL path; `../`, `?` or `#` would redirect the GET
            # to a different path on the vendor host.
            return self._async_poll_failure(submission, "async_run_handle_malformed")
        quoted_handle = urlquote(handle, safe="")

        status_path, result_path = paths
        started = utc_now()
        deadline = monotonic() + self._async_poll_budget_seconds(request)
        per_request_timeout = self._async_poll_request_timeout(request)
        try:
            credential, credential_source = self._credential_resolver.resolve(self.provider_id)
        except CredentialUnavailable:
            return submission

        poll_calls = 0
        status_response: HttpResponse | None = None
        last_failure: FetchOutcome | None = None
        for attempt in range(max(0, self.ASYNC_POLL_MAX_ATTEMPTS)):
            if monotonic() >= deadline:
                return self._async_poll_failure(
                    submission,
                    "async_poll_deadline_exceeded",
                    poll_calls,
                    last_error=last_failure.error_class if last_failure else None,
                )
            fetched = self._get_json(
                status_path.format(handle=quoted_handle),
                credential=credential,
                timeout_seconds=per_request_timeout,
            )
            poll_calls += 1
            if fetched.response is None:
                # A timeout or a 502 on ONE status GET is not the run failing.
                # Returning here threw away every minute already spent polling
                # a job that was still perfectly healthy; the next attempt is
                # already bounded by the attempt cap and the deadline below, so
                # absorb it and poll again.
                last_failure = fetched
                if not fetched.retryable:
                    # 401/403/404 will not become a result by asking again.
                    return self._async_poll_failure(
                        submission, f"async_poll_{fetched.error_class}", poll_calls
                    )
            else:
                last_failure = None
                status_response = fetched.response
                if self._async_run_settled(status_response.body):
                    break
            if attempt + 1 >= self.ASYNC_POLL_MAX_ATTEMPTS:
                if last_failure is not None:
                    # Every retry was spent and the last one still failed. Name
                    # the transport failure rather than reporting a healthy
                    # still-running job.
                    return self._async_poll_failure(
                        submission, f"async_poll_{last_failure.error_class}", poll_calls
                    )
                return self._async_poll_failure(submission, "async_run_pending", poll_calls)
            if monotonic() + self.ASYNC_POLL_INTERVAL_SECONDS >= deadline:
                return self._async_poll_failure(
                    submission,
                    "async_poll_deadline_exceeded",
                    poll_calls,
                    last_error=last_failure.error_class if last_failure else None,
                )
            sleep(self.ASYNC_POLL_INTERVAL_SECONDS)

        if status_response is None:
            # ASYNC_POLL_MAX_ATTEMPTS <= 0: polling is disabled, so the receipt
            # stands rather than raising UnboundLocalError on the check below.
            return self._async_poll_failure(submission, "async_poll_disabled")
        if not self._async_run_succeeded(status_response.body):
            return self._async_poll_failure(
                submission,
                "async_run_not_completed",
                poll_calls,
                observed_status=self._async_run_status_shape(status_response.body),
            )

        # The result fetch is the single most expensive thing to get wrong: the
        # run has completed and been paid for, and a FindAll payload is large
        # enough that the first download is exactly where a timeout or an edge
        # 502 shows up. One unretried GET here discarded a finished run.
        result_response: HttpResponse | None = None
        result_failure: FetchOutcome | None = None
        result_attempts = max(1, self.ASYNC_RESULT_MAX_ATTEMPTS)
        backoff = self.ASYNC_RETRY_BACKOFF_SECONDS
        for attempt in range(result_attempts):
            if monotonic() >= deadline:
                return self._async_poll_failure(
                    submission,
                    "async_poll_deadline_exceeded",
                    poll_calls,
                    last_error=result_failure.error_class if result_failure else None,
                )
            fetched = self._get_json(
                result_path.format(handle=quoted_handle),
                credential=credential,
                timeout_seconds=per_request_timeout,
            )
            poll_calls += 1
            if fetched.response is not None:
                result_response = fetched.response
                break
            result_failure = fetched
            if not fetched.retryable or attempt + 1 >= result_attempts:
                break
            if monotonic() + backoff >= deadline:
                return self._async_poll_failure(
                    submission,
                    "async_poll_deadline_exceeded",
                    poll_calls,
                    last_error=fetched.error_class,
                )
            sleep(backoff)
            backoff = min(backoff * 2, self.ASYNC_RETRY_BACKOFF_MAX_SECONDS)
        if result_response is None:
            # Distinct prefix from the status poll: "the run finished and we
            # could not collect it" is a different operator problem from "we
            # could not tell whether it finished".
            return self._async_poll_failure(
                submission,
                f"async_result_{result_failure.error_class}"
                if result_failure and result_failure.error_class
                else "async_result_unreachable",
                poll_calls,
            )

        poll_call_id = stable_call_id(self.provider_id, request, started)
        sources_list: list[dict[str, Any]] = []
        for idx, item in enumerate(self._extract_items(result_response.body)):
            try:
                sources_list.append(
                    make_source(
                        provider_id=self.provider_id,
                        source_id=f"{poll_call_id}-{idx + 1}",
                        item=self._normalize_item(item),
                    )
                )
            except Exception:
                continue
        resolved = self._result(
            request,
            started_at=started,
            status="ok",
            response={
                "http_status": result_response.status_code,
                "async_submission": True,
                "async_run_handle": handle,
                "async_run_resolved": True,
                "result_count": len(sources_list),
                # `max_provider_calls` is enforced against the number of
                # persisted provider_call records, so a cell that issued N HTTP
                # round trips while counting as 1 makes that budget off by N.
                # The submission record is carried forward and the poll GETs
                # are counted here.
                "async_poll_calls": poll_calls,
                "credential_source": credential_source,
                **dict(self._cost_metadata(result_response.body)),
            },
            sources=tuple(sources_list),
            endpoint=result_path.format(handle=quoted_handle),
        )
        return self._carry_forward(resolved, submission, poll_calls)

    def _async_poll_budget_seconds(self, request: ProviderRequest) -> float:
        """Wall-clock budget for the whole submit-then-poll run.

        Deliberately NOT `request.timeout_seconds`: that is the budget for one
        synchronous HTTP request, and reusing it made the two bounds
        contradict each other. Under the shipped suite config
        (`provider_call_seconds: 60`) a 60s deadline at a 5s cadence allows
        ~12 polls, so `ASYNC_POLL_MAX_ATTEMPTS = 30` was unreachable at any
        timeout this repo ships -- and a FindAll run slower than a synchronous
        request would time out into the pending receipt, which is the exact
        outcome this seam exists to prevent. Worse, `report.py` counts
        `not_applicable` rows in the denominator while only successes reach the
        numerator, so the deadline drove Parallel's entity_resolution and gtm
        success rates toward 0% on a scoring path.

        The poll horizon is therefore its own number, sized so the attempt cap
        is actually reachable: `ASYNC_POLL_MAX_ATTEMPTS` sleeps at
        `ASYNC_POLL_INTERVAL_SECONDS` plus room for each GET.
        `timeout_seconds` continues to bound each individual GET. An explicit
        `poll_budget_seconds` on the request wins when the caller sets one.

        Worst case for a FindAll cell is therefore ASYNC_POLL_BUDGET_SECONDS
        (default 300s) plus one in-flight GET -- size `run_seconds` with that
        in mind.
        """
        explicit = getattr(request, "poll_budget_seconds", None)
        if isinstance(explicit, (int, float)) and explicit > 0:
            return float(explicit)
        return float(self.ASYNC_POLL_BUDGET_SECONDS)

    def _async_poll_request_timeout(self, request: ProviderRequest) -> float:
        """Per-GET timeout: the caller's request budget bounds one round trip."""
        return max(1.0, float(request.timeout_seconds))

    def _async_run_status_shape(self, body: Mapping[str, Any] | list[Any] | str | None) -> Any:
        """The observed status field, so one vendor string change is diagnosable."""
        if not isinstance(body, Mapping):
            return None
        status = body.get("status")
        if isinstance(status, Mapping):
            return {key: status.get(key) for key in ("status", "is_active") if key in status}
        return status

    def _async_poll_failure(
        self,
        submission: ProviderResult,
        error_class: str,
        poll_calls: int = 0,
        *,
        observed_status: Any = None,
        last_error: str | None = None,
    ) -> ProviderResult:
        """Return the pending receipt, but say WHY the poll stopped.

        Collapsing a 401 on the status endpoint, a 404 on the result endpoint,
        an unrecognised run-state string and a genuinely still-running job into
        one `async_run_pending` makes a single vendor-string change
        indistinguishable from normal operation. ``last_error`` carries the
        transport failure that was being retried when a deadline cut the loop
        short, so "we ran out of time" does not hide "we ran out of time being
        rate limited".
        """
        # Deferred: `providers` imports this module at its top, so this
        # direction can only be taken once that module is fully loaded.
        from .providers import _replace_call_record

        record = _replace_call_record(
            submission.call_record,
            error_class=error_class or None,
            response_updates={
                "async_poll_calls": poll_calls,
                **(
                    {"async_observed_status": observed_status}
                    if observed_status is not None
                    else {}
                ),
                **({"async_poll_last_error": last_error} if last_error else {}),
            },
        )
        # Set both sources from the same value. Leaving the frozen field stale
        # while the record carried the real class is the same live-vs-replay
        # divergence the retry_count fix closes, and it would make a caller
        # reading `result.error_class` blind to every poll failure mode this
        # method exists to distinguish. `replace` rather than
        # `object.__setattr__`: a frozen result must not be edited under any
        # caller still holding it.
        return replace(
            submission,
            call_record=record,
            **({"error_class": error_class} if error_class else {}),
        )

    def _carry_forward(
        self, resolved: ProviderResult, submission: ProviderResult, poll_calls: int
    ) -> ProviderResult:
        """Keep the submission acknowledgement as its own billed call record."""
        records: list[dict[str, Any]] = []
        if isinstance(submission.call_record, dict):
            records.append(submission.call_record)
        records.extend(submission.extra_call_records or ())
        return replace(
            resolved,
            extra_call_records=tuple(records + list(resolved.extra_call_records or ())),
        )

    def _get_json(self, path: str, *, credential: str, timeout_seconds: float) -> FetchOutcome:
        """One GET against this provider, keeping the failure instead of erasing it.

        Every caller of this helper is inside a bounded retry loop, and a loop
        cannot decide whether to retry when a 404 and a socket timeout arrive
        as the same ``None``. Classify once here; the loops act on it.
        """
        # Deferred: `providers` imports this module at its top, so this
        # direction can only be taken once that module is fully loaded.
        from .providers import classify_http_status, is_timeout_error

        try:
            response = self._transport.request(
                "GET",
                f"{self._base_url}{path}",
                headers=self._headers(credential),
                json_body=None,
                timeout_seconds=timeout_seconds,
            )
        except (TimeoutError, socket.timeout):
            return FetchOutcome(error_class="timeout", retryable=True)
        except urllib.error.HTTPError as exc:
            return FetchOutcome(
                error_class=f"http_{exc.code}", retryable=self._http_retryable(exc.code)
            )
        except OSError as exc:
            if is_timeout_error(exc):
                return FetchOutcome(error_class="timeout", retryable=True)
            return FetchOutcome(error_class="transport_error", retryable=True)
        if classify_http_status(response.status_code) != "ok":
            return FetchOutcome(
                error_class=f"http_{response.status_code}",
                retryable=self._http_retryable(response.status_code),
            )
        return FetchOutcome(response=response)

    @staticmethod
    def _http_retryable(status_code: int) -> bool:
        """Is another attempt plausible, or is the vendor telling us to stop?

        429 and 5xx are transient by construction; 408 is the server saying the
        request itself timed out. Every other 4xx -- 401 stale credential, 403
        entitlement, 404 run not found -- returns the same answer however many
        times it is asked, and retrying only burns the poll budget.
        """
        return status_code in {408, 429} or status_code >= 500

    def _async_run_settled(self, body: Mapping[str, Any] | list[Any] | str | None) -> bool:
        """Has the run stopped doing work (successfully or not)?"""
        return not self._async_run_active(body)

    def _async_run_active(self, body: Mapping[str, Any] | list[Any] | str | None) -> bool:
        return False

    def _async_run_succeeded(self, body: Mapping[str, Any] | list[Any] | str | None) -> bool:
        """Fail closed: an adapter that declares poll paths must say what
        success looks like. Returning True by default would make a subclass
        that forgets this hook fetch the result endpoint of a run that may not
        have started."""
        return False

    def _async_run_handle(self, body: Mapping[str, Any] | list[Any] | str | None) -> str | None:
        """Return the vendor's run identifier from a submission acknowledgement."""
        if not isinstance(body, Mapping):
            return None
        for key in self.ASYNC_RUN_HANDLE_KEYS:
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
        return None
