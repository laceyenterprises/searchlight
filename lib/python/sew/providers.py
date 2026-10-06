"""Provider adapter skeletons for Search Evaluation Workbench.

The adapters in this module own SEW's provider-facing contract without making
live network access part of normal tests. Real HTTP behavior is injected through
``HttpTransport`` so fixture and classification tests remain offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import urllib.error
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Protocol
from urllib import request as urlrequest

from .async_poll import AsyncRunPollingMixin
from .schema import (
    SCHEMA_VERSION,
    load_document,
    validate_normalized_source,
    validate_provider_call,
)

if TYPE_CHECKING:
    from .domain_surfaces import SurfaceSelection

PROVIDER_CREDENTIALS: Mapping[str, tuple[str, str]] = {
    "exa": ("SEW_EXA_API_KEY", "SEW_EXA_API_KEY_REF"),
    "parallel-web": ("SEW_PARALLEL_WEB_API_KEY", "SEW_PARALLEL_WEB_API_KEY_REF"),
    "firecrawl": ("SEW_FIRECRAWL_API_KEY", "SEW_FIRECRAWL_API_KEY_REF"),
    # Brave keys are plan-scoped: the Search key is refused by the Answers
    # endpoint and vice versa. This adapter is web search, so it takes the
    # Search key (1Password "Brave Search API Key").
    "brave": ("SEW_BRAVE_API_KEY", "SEW_BRAVE_API_KEY_REF"),
    "tavily": ("SEW_TAVILY_API_KEY", "SEW_TAVILY_API_KEY_REF"),
    # One Perplexity key serves both the Search API (this adapter) and the
    # Agent API arm in the agent lane (1Password "Perplexity Search API Key").
    "perplexity": ("SEW_PERPLEXITY_API_KEY", "SEW_PERPLEXITY_API_KEY_REF"),
    # Credential-only entry: the agent lane's Brave Answers arm. Brave keys are
    # plan-scoped, so the Answers endpoint needs the Answers key (1Password
    # "Brave Answers API Key"), not the Search key the `brave` adapter uses.
    "brave-answers": ("SEW_BRAVE_ANSWERS_API_KEY", "SEW_BRAVE_ANSWERS_API_KEY_REF"),
}
LIVE_SMOKE_ENV = "SEW_ALLOW_LIVE_PROVIDER_SMOKE"
DEFAULT_USER_AGENT = "SearchEvaluationWorkbench/1.0"
MAX_HTTP_RESPONSE_BYTES = 20 * 1024 * 1024
SUPPORTED_OPERATIONS = frozenset({"search", "fetch", "crawl", "extract", "findall", "health"})
SCHEMA_STATUS_BY_RESULT_STATUS = {
    "ok": "ok",
    "failed": "failed",
    "rate_limited": "rate_limited",
    "timeout": "timeout",
    "refused": "refused",
    "not_applicable": "not_applicable",
    "unavailable": "failed",
}


class ProviderConfigError(ValueError):
    """Raised when provider configuration is malformed."""


class CredentialUnavailable(RuntimeError):
    """Raised when a provider credential cannot be resolved safely."""


class HttpTransport(Protocol):
    """Minimal injectable transport used by live-capable provider skeletons."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> "HttpResponse":
        """Execute one HTTP request and return a small response object."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Mapping[str, Any] | list[Any] | str | None
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    operations: frozenset[str]
    requires_credential: bool
    fixture_replay: bool = False
    cost_observable: bool = False
    units_observable: bool = False
    domain_surfaces: frozenset[str] = frozenset()
    notes: tuple[str, ...] = ()

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    def as_metadata(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "operations": sorted(self.operations),
            "requires_credential": self.requires_credential,
            "fixture_replay": self.fixture_replay,
            "cost_observable": self.cost_observable,
            "units_observable": self.units_observable,
            "domain_surfaces": sorted(self.domain_surfaces),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ProviderRequest:
    operation: str
    run_id: str
    query: str | None = None
    url: str | None = None
    seed_url: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    # DSB surface evidence for this call. A private channel rather than a
    # reserved key inside ``options``: ``options`` becomes the vendor request
    # body, so an internal key there leaks into the wire (Parallel rejects
    # unknown fields with HTTP 422) and into the audited ``option_keys`` list,
    # and stays correct only while every adapter remembers to strip it.
    domain_surface: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ProviderHealth:
    provider_id: str
    status: str
    checked_at: str
    capabilities: ProviderCapabilities
    detail: str | None = None
    credential_source: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    provider_id: str
    operation: str
    status: str
    started_at: str
    ended_at: str
    retry_count: int
    call_record: dict[str, Any]
    sources: tuple[dict[str, Any], ...] = ()
    error_class: str | None = None
    provider_units: Mapping[str, Any] | None = None
    provider_cost: Mapping[str, Any] | None = None
    redaction_marker: str = "sanitized_excerpt"
    # Additional billed provider_call records produced by the same logical
    # call: the refused attempt behind an entitlement downgrade, and the
    # submission acknowledgement behind a resolved async run.
    #
    # SEAM ONLY -- NOT YET COUNTED. Nothing persists these today: no production
    # code path consumes `ProviderResult.call_record` at all (`harness.py`
    # builds `provider-calls/` from fixture templates), so `metrics.py`'s
    # `provider_calls.total` and the `max_provider_calls` enforcement in
    # `runner.py` are unchanged by this field. The poll-GET count is likewise
    # only in the response body as `async_poll_calls`;
    # `_PROVIDER_CALL_RECORD_KEYS` in schema.py has no slot for it.
    #
    # So an entitlement-probed cell still issues 2 billed searches that a
    # future counter would see as 1, and a resolved FindAll cell issues 1 POST
    # plus 3+ GETs likewise seen as 1. Carrying the records here is what makes
    # the accounting *possible* for the domain runner that lands in DSB-03..06;
    # it does not make it *true* yet. Wiring the persister and adding a
    # validated schema slot is that runner's job -- do not read this field as
    # evidence the operator budget is accurate.
    extra_call_records: tuple[dict[str, Any], ...] = ()

    def schema_provider_call(self) -> dict[str, Any]:
        return validate_provider_call(self.call_record)

    def schema_sources(self) -> tuple[dict[str, Any], ...]:
        return tuple(validate_normalized_source(source) for source in self.sources)


class UrllibTransport:
    """Small stdlib transport used only when explicit live mode is enabled."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers = {**headers, "Content-Type": "application/json"}
        request_headers = {"User-Agent": DEFAULT_USER_AGENT, **headers}
        req = urlrequest.Request(url, data=data, method=method, headers=request_headers)
        with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read(MAX_HTTP_RESPONSE_BYTES).decode("utf-8", errors="replace")
            try:
                body: Mapping[str, Any] | list[Any] | str | None = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            return HttpResponse(response.status, body, dict(response.headers.items()))


class CredentialResolver:
    """Resolve provider credentials from explicit env values or configured refs."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        worker_class: str = "codex",
    ) -> None:
        from .host import credential_environment

        self._host_environ = environ if environ is not None else os.environ
        self._environ = credential_environment(self._host_environ)
        self._worker_class = worker_class

    def resolve(self, provider_id: str) -> tuple[str, str]:
        try:
            value_env, ref_env = PROVIDER_CREDENTIALS[provider_id]
        except KeyError as exc:
            raise ProviderConfigError(
                f"unknown provider credential mapping: {provider_id}"
            ) from exc

        direct_value = self._environ.get(value_env)
        if direct_value:
            return direct_value, value_env

        ref = self._environ.get(ref_env)
        if not ref:
            raise CredentialUnavailable(f"missing {value_env} or {ref_env}")

        if ref.startswith("env:"):
            ref_env_name = ref.removeprefix("env:")
            ref_value = self._environ.get(ref_env_name)
            if ref_value:
                return ref_value, ref_env
            raise CredentialUnavailable(f"{ref_env} points at unset env ref")

        if not ref.startswith("op://"):
            raise CredentialUnavailable(f"{ref_env} must be an op:// or env: reference")

        from .host import HostUnavailable, get_host

        try:
            secret = get_host(self._host_environ).resolve_credential(
                worker_class=self._worker_class,
                ref=ref,
                purpose=f"search-evaluation-workbench {provider_id} provider credential",
            )
        except HostUnavailable as exc:
            raise CredentialUnavailable(str(exc)) from exc
        except Exception as exc:
            raise CredentialUnavailable("provider host refused credential resolution") from exc
        if not secret:
            raise CredentialUnavailable("op credential resolver returned an empty credential")
        return secret, ref_env


class SearchProvider:
    provider_id: str
    capabilities: ProviderCapabilities
    # Domain surfaces this account is known not to be entitled to. Class-level
    # default; ``mark_domain_surface_unavailable`` rebinds it per instance.
    _unavailable_domain_surfaces: frozenset[str] = frozenset()
    # Refusal observations per surface, keyed by surface id. A denial is only
    # latched once this reaches ``ENTITLEMENT_DENIAL_OBSERVATIONS``.
    _domain_surface_refusals: Mapping[str, int] = MappingProxyType({})

    # Only these error classes are entitlement evidence. `classify_http_status`
    # also maps 451 to `refused`, but 451 is a legal/jurisdictional block on the
    # *content*, not a statement about what this plan bought -- latching on it
    # would move a legal-domain cell off its specialist arm for the wrong
    # reason. Anything else (timeouts, rate limits, WAF noise) is inconclusive.
    ENTITLEMENT_DENIAL_ERROR_CLASSES: frozenset[str] = frozenset({"http_401", "http_403"})
    # One refusal is not an entitlement fact. A single CDN/WAF 403 during a
    # burst would otherwise rewrite every remaining cell for that vendor and
    # surface in the run, and the report would state denial as fact.
    ENTITLEMENT_DENIAL_OBSERVATIONS: int = 2

    @property
    def domain_surface_refusals(self) -> Mapping[str, int]:
        """Refusal observations counted per surface, so a denial is auditable."""
        return self._domain_surface_refusals

    @property
    def unavailable_domain_surfaces(self) -> frozenset[str]:
        """Surfaces this key cannot reach, so resolution picks the generic arm.

        Capability metadata declares what the *vendor* ships; it cannot know
        what this *account's plan tier* has bought. This set carries that second
        fact. It is filled in by ``search_domain`` when a surface-specific call
        is refused while the generic arm succeeds, and an operator who already
        knows a plan excludes a surface can pre-seed it to skip the probe.

        LIFETIME -- this is **per provider instance**, not per run, and the
        existing lane pattern constructs a fresh provider inside every cell
        (``retrieval_lane.py``: ``provider = make_provider(provider_id,
        live_enabled=True)``). A domain runner that copies that pattern
        verbatim discards this memo after each cell, so every gated cell pays
        for the probe again and the "skip the probe" affordance never engages.
        A runner that wants the memo to hold for a run must either hoist
        provider construction to (provider, run) scope, or carry the set itself
        and pass it back in -- ``resolve_domain_surface`` takes
        ``unavailable_surfaces`` explicitly for exactly that reason, so the
        lifetime is the runner's choice rather than an accident of instance
        reuse.
        """
        return self._unavailable_domain_surfaces

    def mark_domain_surface_unavailable(self, surface_id: str) -> None:
        """Record that this account is not entitled to ``surface_id``."""
        self._unavailable_domain_surfaces = self._unavailable_domain_surfaces | {surface_id}

    def record_domain_surface_refusal(self, surface_id: str) -> int:
        """Count one corroborated refusal; latch only at the observation floor.

        Returns the running count so the caller can persist it as the evidence
        behind a ``entitlement_denied`` downgrade.
        """
        counts = dict(self._domain_surface_refusals)
        observations = counts.get(surface_id, 0) + 1
        counts[surface_id] = observations
        self._domain_surface_refusals = MappingProxyType(counts)
        if observations >= self.ENTITLEMENT_DENIAL_OBSERVATIONS:
            self.mark_domain_surface_unavailable(surface_id)
        return observations

    def search_domain(
        self,
        query: str,
        *,
        domain: str,
        run_id: str,
        timeout_seconds: float = 30.0,
        surface_context: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> ProviderResult:
        """Search through the provider's declared surface for one DSB domain."""
        from .domain_surfaces import (
            DomainSurfaceError,
            DOWNGRADE_ENTITLEMENT_DENIED,
            generic_surface_selection,
            resolve_domain_surface,
        )

        started = utc_now()
        try:
            selection = resolve_domain_surface(
                self.provider_id,
                domain,
                self.capabilities,
                context=surface_context,
                unavailable_surfaces=self.unavailable_domain_surfaces,
            )
        except DomainSurfaceError:
            # Every other refusal in this class returns a ProviderResult with an
            # error_class; an unknown provider/domain pair must not be the one
            # path that raises through a runner mid-matrix.
            return make_not_applicable_result(
                provider_id=self.provider_id,
                request=ProviderRequest(
                    "search",
                    run_id=run_id,
                    query=query,
                    options=options,
                    timeout_seconds=timeout_seconds,
                ),
                started_at=started,
                capabilities=self.capabilities,
                error_class="undeclared_domain_surface",
            )

        result = self._call_domain_surface(
            query, selection, run_id=run_id, timeout_seconds=timeout_seconds, options=options
        )
        if selection.generic_surface or not self._is_entitlement_refusal(result):
            return result

        # Plan tier gates several vendor surfaces, and a gated surface answers a
        # surface-specific request with 401/403. Recording that as a provider
        # failure charges the vendor with a retrieval miss for an entitlement we
        # never bought -- the SPEC requires `generic_surface`, not `failed`.
        # Distinguish the two possible causes by retrying the generic arm. Only
        # a clean `ok` there proves the credential works and the refusal was the
        # surface; anything else (the account itself refused, or an inconclusive
        # timeout/rate limit) is not evidence of an entitlement gap, so the
        # original refusal stands and nothing is remembered. Marking a surface
        # unavailable on a timeout would poison every later cell in the run.
        downgraded = generic_surface_selection(
            self.provider_id,
            domain,
            reason=DOWNGRADE_ENTITLEMENT_DENIED,
            declared_surface_id=selection.surface_id,
        )
        retry = self._call_domain_surface(
            query,
            downgraded,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            options=options,
            # The refused attempt is the only evidence for the downgrade this
            # record asserts, and returning `retry` alone would drop it: its
            # call id, status and error class would never reach the evidence
            # bundle, and the second billed round trip would be invisible in
            # both cost and retry accounting.
            downgrade_evidence=self._refusal_evidence(result),
            retry_count=1,
        )
        if retry.status != "ok":
            return result
        observations = self.record_domain_surface_refusal(selection.surface_id)
        stamped = self._stamp_surface_observations(retry, observations)
        # Two billed requests happened. Returning only the retry would leave
        # `provider_calls.total` -- the number `max_provider_calls` is enforced
        # against -- counting one, so an entitlement-gated run would issue
        # twice the requests the operator budgeted for.
        if isinstance(result.call_record, dict):
            return replace(
                stamped,
                extra_call_records=(result.call_record,) + tuple(stamped.extra_call_records or ()),
            )
        return stamped

    def _call_domain_surface(
        self,
        query: str,
        selection: SurfaceSelection,
        *,
        run_id: str,
        timeout_seconds: float,
        options: Mapping[str, Any],
        downgrade_evidence: Mapping[str, Any] | None = None,
        retry_count: int = 0,
    ) -> ProviderResult:
        surface_record = selection.record()
        if downgrade_evidence:
            surface_record["downgrade_evidence"] = dict(downgrade_evidence)
        provider_request = ProviderRequest(
            selection.operation,
            run_id=run_id,
            query=query,
            options=selection.apply(options),
            timeout_seconds=timeout_seconds,
            domain_surface=surface_record,
        )
        result = self.call(provider_request)
        # An accepted async submission is a receipt, not a result set. Resolve
        # it here so a domain cell returns retrieved sources like any other;
        # `resolve_async_run` is bounded and returns the receipt untouched when
        # the run has not settled, so this never does worse than terminating on
        # the acknowledgement.
        if result.error_class == "async_run_pending":
            result = self.resolve_async_run(result, request=provider_request)
        # Stamped after the fact rather than threaded through `call`, whose
        # signature is shared by the fixture and HTTP providers alike. Both
        # sources are set from the same value: leaving the frozen
        # `ProviderResult.retry_count` at 0 while the record said 1 made live
        # and fixture replay of the same record disagree, since
        # `FixtureProvider.call` reads the number back out of the record.
        if retry_count:
            result = replace(
                result,
                retry_count=retry_count,
                call_record=_replace_call_record(result.call_record, retry_count=retry_count),
            )
        return result

    def resolve_async_run(
        self, submission: ProviderResult, *, request: ProviderRequest, sleep: Any = None
    ) -> ProviderResult:
        """No poll seam on the base provider; the receipt stands."""
        return submission

    def _is_entitlement_refusal(self, result: ProviderResult) -> bool:
        """Is this refusal evidence about the plan, rather than about content?"""
        return (
            result.status == "refused"
            and result.error_class in self.ENTITLEMENT_DENIAL_ERROR_CLASSES
        )

    @staticmethod
    def _refusal_evidence(result: ProviderResult) -> dict[str, Any]:
        """Minimal, auditable trace of the refusal that justified a downgrade."""
        response = result.call_record.get("response", {}) if result.call_record else {}
        return {
            "call_id": (result.call_record or {}).get("call_id"),
            "http_status": response.get("http_status") if isinstance(response, Mapping) else None,
            "error_class": result.error_class,
            "status": result.status,
        }

    def _stamp_surface_observations(
        self, result: ProviderResult, observations: int
    ) -> ProviderResult:
        """Persist how many refusals back an ``entitlement_denied`` claim."""
        record = result.call_record
        if not isinstance(record, dict):
            return result
        response = record.get("response")
        if not isinstance(response, dict):
            return result
        surface = response.get("domain_surface")
        if not isinstance(surface, dict):
            return result
        surface["refusal_observations"] = observations
        surface["entitlement_latched"] = observations >= self.ENTITLEMENT_DENIAL_OBSERVATIONS
        return result

    def findall(
        self,
        objective: str,
        *,
        run_id: str,
        timeout_seconds: float = 30.0,
        **options: Any,
    ) -> ProviderResult:
        return self.call(
            ProviderRequest(
                "findall",
                run_id=run_id,
                query=objective,
                options=options,
                timeout_seconds=timeout_seconds,
            )
        )

    def search(
        self, query: str, *, run_id: str, timeout_seconds: float = 30.0, **options: Any
    ) -> ProviderResult:
        return self.call(
            ProviderRequest(
                "search",
                run_id=run_id,
                query=query,
                options=options,
                timeout_seconds=timeout_seconds,
            )
        )

    def fetch(
        self, url: str, *, run_id: str, timeout_seconds: float = 30.0, **options: Any
    ) -> ProviderResult:
        return self.call(
            ProviderRequest(
                "fetch",
                run_id=run_id,
                url=url,
                options=options,
                timeout_seconds=timeout_seconds,
            )
        )

    def crawl(
        self, seed_url: str, *, run_id: str, timeout_seconds: float = 30.0, **options: Any
    ) -> ProviderResult:
        return self.call(
            ProviderRequest(
                "crawl",
                run_id=run_id,
                seed_url=seed_url,
                options=options,
                timeout_seconds=timeout_seconds,
            )
        )

    def extract(
        self, url: str, *, run_id: str, timeout_seconds: float = 30.0, **options: Any
    ) -> ProviderResult:
        return self.call(
            ProviderRequest(
                "extract",
                run_id=run_id,
                url=url,
                options=options,
                timeout_seconds=timeout_seconds,
            )
        )

    def call(self, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError

    def health(self) -> ProviderHealth:
        raise NotImplementedError


class HttpProvider(AsyncRunPollingMixin, SearchProvider):
    """Capability-aware skeleton for the HTTP search providers."""

    def __init__(
        self,
        provider_id: str,
        *,
        capabilities: ProviderCapabilities,
        base_url: str,
        endpoints: Mapping[str, str],
        credential_resolver: CredentialResolver | None = None,
        transport: HttpTransport | None = None,
        live_enabled: bool | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.capabilities = capabilities
        self._base_url = base_url.rstrip("/")
        self._endpoints = endpoints
        self._credential_resolver = credential_resolver or CredentialResolver()
        self._transport = transport or UrllibTransport()
        self._live_enabled = (
            os.environ.get(LIVE_SMOKE_ENV) == "1" if live_enabled is None else live_enabled
        )

    def health(self) -> ProviderHealth:
        checked_at = utc_now()
        if not self._live_enabled:
            return ProviderHealth(
                provider_id=self.provider_id,
                status="not_applicable",
                checked_at=checked_at,
                capabilities=self.capabilities,
                detail=f"live smoke disabled; set {LIVE_SMOKE_ENV}=1",
            )
        try:
            _, source = self._credential_resolver.resolve(self.provider_id)
        except CredentialUnavailable as exc:
            return ProviderHealth(
                provider_id=self.provider_id,
                status="unavailable",
                checked_at=checked_at,
                capabilities=self.capabilities,
                detail=str(exc),
            )
        return ProviderHealth(
            provider_id=self.provider_id,
            status="ok",
            checked_at=checked_at,
            capabilities=self.capabilities,
            credential_source=source,
        )

    def call(self, request: ProviderRequest) -> ProviderResult:
        started = utc_now()
        if request.operation not in SUPPORTED_OPERATIONS:
            raise ValueError(f"unknown provider operation: {request.operation}")
        if request.operation == "health":
            health = self.health()
            return self._result(
                request,
                started_at=started,
                status=health.status,
                response={
                    "checked_at": health.checked_at,
                    "capabilities": self.capabilities.as_metadata(),
                    "detail": health.detail,
                    "credential_source": health.credential_source,
                },
                error_class=None if health.status == "ok" else health.status,
            )
        if not self.capabilities.supports(request.operation):
            return self._result(
                request,
                started_at=started,
                status="not_applicable",
                response={"capabilities": self.capabilities.as_metadata()},
                error_class="unsupported_operation",
            )
        if not self._live_enabled:
            return self._result(
                request,
                started_at=started,
                status="not_applicable",
                response={"live_enabled": False, "required_env": LIVE_SMOKE_ENV},
                error_class="live_smoke_disabled",
            )
        try:
            credential, credential_source = self._credential_resolver.resolve(self.provider_id)
        except CredentialUnavailable as exc:
            return self._result(
                request,
                started_at=started,
                status="unavailable",
                response={"credential": "unavailable"},
                error_class="missing_credential",
                error_detail=str(exc),
            )

        endpoint = self._endpoints[request.operation]
        try:
            response = self._transport.request(
                "POST",
                f"{self._base_url}{endpoint}",
                headers=self._headers(credential),
                json_body=self._request_body(request),
                timeout_seconds=request.timeout_seconds,
            )
        except TimeoutError:
            return self._result(
                request,
                started_at=started,
                status="timeout",
                error_class="timeout",
                endpoint=endpoint,
            )
        except socket.timeout:
            return self._result(
                request,
                started_at=started,
                status="timeout",
                error_class="timeout",
                endpoint=endpoint,
            )
        except urllib.error.HTTPError as exc:
            return self._result(
                request,
                started_at=started,
                status=classify_http_status(exc.code),
                response={"http_status": exc.code, **http_error_body(exc)},
                error_class=f"http_{exc.code}",
                endpoint=endpoint,
            )
        except OSError as exc:
            if is_timeout_error(exc):
                return self._result(
                    request, started_at=started, status="timeout", error_class="timeout"
                )
            return self._result(
                request,
                started_at=started,
                status="failed",
                response={"transport_error": exc.__class__.__name__},
                error_class="transport_error",
                endpoint=endpoint,
            )

        status = classify_http_status(response.status_code)
        call_id = stable_call_id(self.provider_id, request, started)
        if status == "ok" and request.operation in self.ASYNC_SUBMISSION_OPERATIONS:
            return self._async_submission_result(
                request,
                started_at=started,
                call_id=call_id,
                response=response,
                credential_source=credential_source,
                endpoint=endpoint,
            )
        # A single unrepresentable result must not discard the whole response:
        # dropping N-1 good sources because the Nth had a malformed URL would
        # read downstream as a weak provider rather than as a parse failure.
        sources_list: list[dict[str, Any]] = []
        dropped = 0
        for idx, item in enumerate(self._extract_items(response.body)):
            try:
                sources_list.append(
                    make_source(
                        provider_id=self.provider_id,
                        source_id=f"{call_id}-{idx + 1}",
                        item=self._normalize_item(item),
                    )
                )
            except Exception:
                dropped += 1
        sources = tuple(sources_list)
        # A normalization failure is an ADAPTER defect, not weak retrieval. If
        # every extracted item was dropped, returning a clean `ok` with zero
        # results would misattribute our parse bug to the provider's quality:
        # downstream scoring would see ordinary misses. Degrade the call so the
        # failure is visible where it belongs. A genuinely empty result set
        # (nothing extracted, nothing dropped) is untouched.
        extracted_count = len(sources_list) + dropped
        parse_error: str | None = None
        if dropped and extracted_count and not sources_list:
            status = "failed"
            parse_error = "source_normalization_failed"
        elif dropped:
            parse_error = "source_normalization_partial"
        return self._result(
            request,
            started_at=started,
            call_id=call_id,
            status=status,
            response={
                "http_status": response.status_code,
                "result_count": len(sources),
                "dropped_source_count": dropped,
                **({"parse_warning": parse_error} if parse_error else {}),
                "credential_source": credential_source,
                **self._cost_metadata(response.body),
            },
            sources=sources,
            error_class=(
                "source_normalization_failed"
                if parse_error == "source_normalization_failed"
                else (None if status == "ok" else f"http_{response.status_code}")
            ),
            endpoint=endpoint,
        )

    def _async_submission_result(
        self,
        request: ProviderRequest,
        *,
        started_at: str,
        call_id: str,
        response: HttpResponse,
        credential_source: str | None,
        endpoint: str | None = None,
    ) -> ProviderResult:
        """Terminate a run-submission acknowledgement in its own non-``ok`` state.

        An accepted async run is not a result set. The body carries no items, so
        the ordinary path would emit ``ok`` with ``result_count: 0`` and scoring
        would read a run that has not even started as a zero-recall retrieval --
        the same misattribution the normalization guard below exists to prevent.
        The run handle is persisted unconditionally, and its absence is a loud
        ``failed`` rather than a silent unrecoverable cell.
        """
        run_handle = self._async_run_handle(response.body)
        cost_metadata = dict(self._cost_metadata(response.body))
        if run_handle:
            units = dict(cost_metadata.get("provider_units", {}))
            units.setdefault("async_run_handle", run_handle)
            cost_metadata["provider_units"] = units
        return self._result(
            request,
            started_at=started_at,
            call_id=call_id,
            status="not_applicable" if run_handle else "failed",
            response={
                "http_status": response.status_code,
                "async_submission": True,
                "async_run_handle": run_handle,
                "credential_source": credential_source,
                **cost_metadata,
            },
            endpoint=endpoint,
            error_class="async_run_pending" if run_handle else "async_run_handle_missing",
            error_detail=(
                None
                if run_handle
                else (
                    f"{self.provider_id} {request.operation} acknowledgement carried no run "
                    f"identifier; expected one of {list(self.ASYNC_RUN_HANDLE_KEYS)}"
                )
            ),
        )

    def _headers(self, credential: str) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {credential}"}

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        body: dict[str, Any] = dict(request.options)
        if request.query is not None:
            body["query"] = request.query
        if request.url is not None:
            body["url"] = request.url
        if request.seed_url is not None:
            body["url"] = request.seed_url
        return body

    def _extract_items(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> tuple[Mapping[str, Any], ...]:
        """Locate the per-result records in a provider response payload."""
        return extract_items(body)

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        """Map one provider-shaped result onto the fields ``make_source`` reads."""
        return item

    def _cost_metadata(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> dict[str, Mapping[str, Any]]:
        """Extract provider-reported spend/unit metadata from a response payload."""
        return extract_cost_metadata(body)

    def _result(
        self,
        request: ProviderRequest,
        *,
        started_at: str,
        status: str,
        response: Mapping[str, Any] | None = None,
        sources: tuple[dict[str, Any], ...] = (),
        error_class: str | None = None,
        error_detail: str | None = None,
        call_id: str | None = None,
        endpoint: str | None = None,
    ) -> ProviderResult:
        ended = utc_now()
        response_metadata = dict(response or {})
        if isinstance(request.domain_surface, Mapping):
            surface_record = dict(request.domain_surface)
            # "Surface used" must report the observation, not the declaration:
            # the registry literal and the adapter's endpoint map can drift, and
            # `hq sew domains explain` prints this field as evidence. A cell
            # that never issued a request (unsupported operation, live mode
            # disabled, missing credential) reaches here with `endpoint=None`
            # and records no endpoint at all, because naming one it never
            # called would be the same false observation in the other
            # direction.
            if endpoint is not None:
                surface_record["endpoint"] = endpoint
            response_metadata["domain_surface"] = surface_record
        call_record = make_call_record(
            provider_id=self.provider_id,
            request=request,
            started_at=started_at,
            ended_at=ended,
            status=status,
            response=response_metadata,
            sources=sources,
            error_class=error_class,
            error_detail=error_detail,
            call_id=call_id,
        )
        cost_metadata = provider_result_cost_metadata(response_metadata)
        return ProviderResult(
            provider_id=self.provider_id,
            operation=request.operation,
            status=status,
            started_at=started_at,
            ended_at=ended,
            retry_count=0,
            call_record=call_record,
            sources=sources,
            error_class=error_class,
            provider_units=cost_metadata.get("provider_units"),
            provider_cost=cost_metadata.get("provider_cost"),
        )


class FixtureProvider(SearchProvider):
    """Replay sanitized SEW provider fixtures from a run directory."""

    provider_id = "fixture"
    capabilities = ProviderCapabilities(
        provider_id="fixture",
        # `findall` is replayable like any other operation: the fixture is the
        # persisted call record, so a recorded DSB findall cell must not be
        # gated out of offline replay by `cell_applicability`.
        operations=frozenset({"search", "fetch", "crawl", "extract", "findall", "health"}),
        requires_credential=False,
        fixture_replay=True,
        notes=("offline fixture replay",),
    )

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir
        self._consumed_calls: set[Path] = set()

    def health(self) -> ProviderHealth:
        status = "ok" if self._run_dir.is_dir() else "unavailable"
        return ProviderHealth(
            provider_id=self.provider_id,
            status=status,
            checked_at=utc_now(),
            capabilities=self.capabilities,
            detail=str(self._run_dir),
        )

    def call(self, request: ProviderRequest) -> ProviderResult:
        started = utc_now()
        calls_dir = self._run_dir / "provider-calls"
        for call_path in sorted(calls_dir.glob("*.json")):
            if call_path in self._consumed_calls:
                continue
            call_record = load_document(call_path)
            if call_record.get("operation") != request.operation:
                continue
            sources = tuple(
                validate_normalized_source(load_document(self._run_dir / ref))
                for ref in call_record.get("normalized_source_refs", [])
            )
            validated_call = validate_provider_call(call_record)
            cost_metadata = provider_result_cost_metadata(validated_call.get("response", {}))
            self._consumed_calls.add(call_path)
            return ProviderResult(
                provider_id="fixture",
                operation=request.operation,
                status=validated_call["status"],
                started_at=validated_call["started_at"],
                ended_at=validated_call["ended_at"],
                retry_count=validated_call["retry_count"],
                call_record=validated_call,
                sources=sources,
                error_class=validated_call.get("error_class"),
                provider_units=cost_metadata.get("provider_units"),
                provider_cost=cost_metadata.get("provider_cost"),
            )
        return make_not_applicable_result(
            provider_id="fixture",
            request=request,
            started_at=started,
            capabilities=self.capabilities,
            error_class="fixture_missing_operation",
        )

    def search_domain(
        self,
        query: str,
        *,
        domain: str,
        run_id: str,
        timeout_seconds: float = 30.0,
        surface_context: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> ProviderResult:
        """Replay a recorded domain cell offline.

        The base implementation resolves against `SURFACE_DECLARATIONS`, which
        has no `("fixture", ...)` key, so every fixture-mode domain cell would
        otherwise return `undeclared_domain_surface` -- and offline replay is
        this module's core premise.

        Re-resolving would also be wrong on its own terms: the surface a cell
        used is a property of the RECORDED run, and re-deriving it at replay
        time would let a later registry edit silently rewrite history. The
        recorded call is replayed as-is, `domain_surface` block included. Both
        operations a domain cell can record are tried, since a FindAll cell
        records `findall` and every other records `search`; a miss consumes no
        fixture.
        """
        result = make_not_applicable_result(
            provider_id="fixture",
            request=ProviderRequest(
                "search",
                run_id=run_id,
                query=query,
                options=dict(options),
                timeout_seconds=timeout_seconds,
            ),
            started_at=utc_now(),
            capabilities=self.capabilities,
            error_class="fixture_missing_operation",
        )
        for operation in ("search", "findall"):
            replayed = self.call(
                ProviderRequest(
                    operation,
                    run_id=run_id,
                    query=query,
                    options=dict(options),
                    timeout_seconds=timeout_seconds,
                )
            )
            if replayed.error_class != "fixture_missing_operation":
                return replayed
            result = replayed
        return result


def make_provider(
    provider_id: str,
    *,
    credential_resolver: CredentialResolver | None = None,
    transport: HttpTransport | None = None,
    live_enabled: bool | None = None,
    fixture_run_dir: Path | None = None,
) -> SearchProvider:
    if provider_id == "fixture":
        if fixture_run_dir is None:
            raise ProviderConfigError("fixture provider requires fixture_run_dir")
        return FixtureProvider(fixture_run_dir)
    if provider_id == "exa":
        return ExaProvider(
            credential_resolver=credential_resolver,
            transport=transport,
            live_enabled=live_enabled,
        )
    if provider_id == "parallel-web":
        return ParallelWebProvider(
            credential_resolver=credential_resolver,
            transport=transport,
            live_enabled=live_enabled,
        )
    if provider_id == "firecrawl":
        return FirecrawlProvider(
            credential_resolver=credential_resolver,
            transport=transport,
            live_enabled=live_enabled,
        )
    if provider_id == "brave":
        return BraveProvider(
            credential_resolver=credential_resolver,
            transport=transport,
            live_enabled=live_enabled,
        )
    if provider_id == "tavily":
        return TavilyProvider(
            credential_resolver=credential_resolver,
            transport=transport,
            live_enabled=live_enabled,
        )
    if provider_id == "perplexity":
        return PerplexityProvider(
            credential_resolver=credential_resolver,
            transport=transport,
            live_enabled=live_enabled,
        )
    raise ProviderConfigError(f"unknown provider_id: {provider_id}")


class ExaProvider(HttpProvider):
    """Exa neural search.

    Wire format verified live 2026-09-19 against ``https://api.exa.ai``:
    ``POST /search`` authenticates with ``x-api-key`` and takes
    ``{query, type, numResults, contents}``. ``contents.highlights`` is the
    recommended output shape; Exa returns query-relevant excerpts as a list of
    strings per result, which is flattened here into the common snippet field.
    Spend is reported in ``costDollars`` and index latency in ``searchTime``.
    """

    # `type` selects retrieval depth: auto | fast | instant | deep-lite | deep |
    # deep-reasoning. `fast` is the latency-matched peer of Parallel's `fast`
    # mode; `auto` is the product default. Suites pin this explicitly so a
    # provider's default depth never silently becomes the comparison.
    DEFAULT_SEARCH_TYPE = "auto"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "exa",
            capabilities=ProviderCapabilities(
                provider_id="exa",
                operations=frozenset({"search", "fetch", "health"}),
                requires_credential=True,
                cost_observable=True,
                units_observable=True,
                # `github` is deliberately absent: Exa deprecated it
                # (changelog 2026-07-23, re-verified 2026-09-21) and unknown
                # categories are absorbed as free-text hints rather than
                # refused, so declaring it would assert a specialist surface
                # that no gate could ever catch as missing.
                domain_surfaces=frozenset({"exa.category.people", "exa.category.company"}),
                notes=("neural ranked search with query-relevant highlights",),
            ),
            base_url="https://api.exa.ai",
            endpoints={"search": "/search", "fetch": "/contents"},
            **kwargs,
        )

    def _headers(self, credential: str) -> Mapping[str, str]:
        return {"x-api-key": credential}

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        options = dict(request.options)
        max_chars = options.pop("max_chars_per_result", None)
        num_results = options.pop("num_results", None)
        search_type = options.pop("mode", None) or self.DEFAULT_SEARCH_TYPE
        # Exa offers two content views and they are NOT interchangeable:
        #   highlights -> query-relevant excerpts chosen by relevance
        #   text       -> the page body from the top, truncated at maxCharacters
        # `text` is positional, so on a docs page the first N chars are nav and
        # boilerplate and a specific value deeper in the page is cut off. The
        # honest peer of Parallel's `excerpts` (query-relevant) is `highlights`,
        # not `text`; defaulting to `text` merely because a character budget was
        # supplied silently handicaps Exa on exactly the lookup tasks it should
        # win. So the view is chosen explicitly and defaults to highlights.
        # /search nests content options under `contents`; /contents takes them
        # at the top level next to `urls`. Requesting both highlights and text
        # bills two views of the same page, so pick exactly one.
        content_view = options.pop("content_view", None) or "highlights"
        if content_view == "text":
            contents: dict[str, Any] = {
                "text": {"maxCharacters": int(max_chars)} if max_chars else True
            }
        else:
            contents = {"highlights": True}
        if request.operation == "fetch":
            body: dict[str, Any] = {"urls": [request.url], **contents}
            body.update(options)
            return body
        body = {"query": request.query, "type": search_type, "contents": contents}
        if num_results is not None:
            body["numResults"] = int(num_results)
        body.update(options)
        return body

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = dict(item)
        highlights = item.get("highlights")
        if isinstance(highlights, list) and highlights:
            normalized["snippet"] = "\n\n".join(str(part) for part in highlights)
        return normalized

    def _cost_metadata(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> dict[str, Mapping[str, Any]]:
        metadata = dict(extract_cost_metadata(body))
        if not isinstance(body, Mapping):
            return metadata
        cost = body.get("costDollars")
        if isinstance(cost, Mapping):
            metadata["provider_cost"] = {"currency": "USD", **dict(cost)}
        units: dict[str, Any] = {}
        if body.get("searchTime") is not None:
            units["search_time_ms"] = body.get("searchTime")
        if body.get("resolvedSearchType"):
            units["resolved_search_type"] = body.get("resolvedSearchType")
        if body.get("requestId"):
            units["request_id"] = body.get("requestId")
        if units:
            metadata["provider_units"] = {**dict(metadata.get("provider_units", {})), **units}
        return metadata


class ParallelWebProvider(HttpProvider):
    """Parallel Web Systems Search/Extract.

    Wire format verified live 2026-09-19 against ``https://api.parallel.ai``:
    ``POST /v1/search`` authenticates with ``x-api-key`` (not bearer) and takes
    an ``objective`` plus ``search_queries`` rather than a single ``query``.
    Per-result evidence arrives as an ``excerpts`` list, and result-count and
    excerpt sizing live under ``advanced_settings``, not at the top level --
    a stray top-level ``max_results`` is rejected with HTTP 422.

    Verified against the published API reference 2026-09-21: ``source_policy``
    nests under ``advanced_settings`` for ``/v1/search`` (docs.parallel.ai
    "Source Policy" documents exactly this body), so the DSB legal and
    code_and_pr arms send the documented shape rather than an inferred one.

    FindAll is submit-then-poll, verified 2026-09-21 against the FindAll
    quickstart: ``POST /v1beta/findall/runs`` returns ``findall_id``,
    ``GET /v1beta/findall/runs/{id}`` reports ``status.is_active``, and
    ``GET /v1beta/findall/runs/{id}/result`` returns ``candidates``. All three
    authenticate with ``x-api-key`` alone.
    """

    # turbo ~200ms p50, fast ~700ms, advanced ~3s (the API default). Mode is the
    # dominant latency term for this provider, so it is always set explicitly:
    # comparing Parallel's default `advanced` against another provider's fast
    # path is the single easiest way to produce a misleading latency table.
    DEFAULT_MODE = "fast"

    # FindAll is submit-then-poll: POST returns a run handle, not results.
    ASYNC_SUBMISSION_OPERATIONS = frozenset({"findall"})
    ASYNC_RUN_HANDLE_KEYS = ("findall_id", "run_id", "id")
    ASYNC_POLL_PATHS = MappingProxyType(
        {
            "findall": (
                "/v1beta/findall/runs/{handle}",
                "/v1beta/findall/runs/{handle}/result",
            )
        }
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "parallel-web",
            capabilities=ProviderCapabilities(
                provider_id="parallel-web",
                operations=frozenset({"search", "fetch", "findall", "health"}),
                requires_credential=True,
                cost_observable=False,
                units_observable=True,
                domain_surfaces=frozenset({"parallel.search.source_policy", "parallel.findall"}),
                notes=("objective-driven search with turbo/fast/advanced modes",),
            ),
            base_url="https://api.parallel.ai",
            endpoints={
                "search": "/v1/search",
                "fetch": "/v1/extract",
                "findall": "/v1beta/findall/runs",
            },
            **kwargs,
        )

    def _headers(self, credential: str) -> Mapping[str, str]:
        return {"x-api-key": credential}

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        options = dict(request.options)
        max_chars = options.pop("max_chars_per_result", None)
        num_results = options.pop("num_results", None)
        mode = options.pop("mode", None) or self.DEFAULT_MODE
        if request.operation == "findall":
            # FindAll shapes its own run: retrieval depth and per-result sizing
            # are search knobs it does not accept, so the three popped above are
            # dropped here deliberately rather than forwarded into a 422.
            body = {"objective": request.query}
            body.update(options)
            return body
        if request.operation == "fetch":
            body: dict[str, Any] = {"urls": [request.url]}
            body.update(options)
            return body
        objective = options.pop("objective", None) or request.query
        queries = options.pop("search_queries", None) or ([request.query] if request.query else [])
        advanced: dict[str, Any] = dict(options.pop("advanced_settings", {}) or {})
        if num_results is not None:
            advanced["max_results"] = int(num_results)
        if max_chars:
            excerpt_settings = dict(advanced.get("excerpt_settings", {}) or {})
            excerpt_settings["max_chars_per_result"] = int(max_chars)
            advanced["excerpt_settings"] = excerpt_settings
        body = {"objective": objective, "search_queries": list(queries), "mode": mode}
        if advanced:
            body["advanced_settings"] = advanced
        body.update(options)
        return body

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = dict(item)
        excerpts = item.get("excerpts")
        if isinstance(excerpts, list) and excerpts:
            normalized["snippet"] = "\n\n".join(str(part) for part in excerpts)
        if item.get("publish_date") is not None:
            normalized["published_at"] = item.get("publish_date")
        return normalized

    def _async_run_active(self, body: Mapping[str, Any] | list[Any] | str | None) -> bool:
        """Is the FindAll run still working?

        Run state nests under ``status``. Only a positive ``is_active: true``
        keeps the poll loop going; an unreadable payload settles immediately
        and then fails ``_async_run_succeeded``, so the caller gets the pending
        receipt back rather than results that may not exist.
        """
        if not isinstance(body, Mapping):
            return False
        status = body.get("status")
        if isinstance(status, Mapping) and isinstance(status.get("is_active"), bool):
            return bool(status["is_active"])
        if isinstance(body.get("is_active"), bool):
            return bool(body["is_active"])
        # No readable run state. Keep polling only while the vendor positively
        # says the run is still working; anything else settles immediately and
        # fails the success check below, so an unrecognised payload returns the
        # pending receipt instead of burning the whole poll budget on sleeps.
        return False

    def _async_run_succeeded(self, body: Mapping[str, Any] | list[Any] | str | None) -> bool:
        """Did the settled run complete, rather than cancel or fail?"""
        if not isinstance(body, Mapping):
            return False
        status = body.get("status")
        if isinstance(status, Mapping):
            return status.get("status") == "completed"
        return body.get("status") == "completed"

    def _extract_items(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> tuple[Mapping[str, Any], ...]:
        """FindAll results arrive as ``candidates``; ordinary search does not.

        Only ``matched`` candidates are sources: FindAll also returns
        considered-and-rejected entities, and counting those as retrieved
        results would inflate Parallel's recall with rows it itself rejected.
        """
        if isinstance(body, Mapping) and isinstance(body.get("candidates"), list):
            return tuple(
                item
                for item in body["candidates"]
                if isinstance(item, Mapping) and item.get("match_status") == "matched"
            )
        return super()._extract_items(body)

    def _cost_metadata(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> dict[str, Mapping[str, Any]]:
        metadata = dict(extract_cost_metadata(body))
        if not isinstance(body, Mapping):
            return metadata
        units = dict(metadata.get("provider_units", {}))
        if body.get("search_id"):
            units["search_id"] = body.get("search_id")
        # Only the unambiguous key here: `_cost_metadata` runs on every
        # operation, and the looser `ASYNC_RUN_HANDLE_KEYS` fallbacks would
        # mislabel a search response's own `id`. The operation-aware,
        # unconditional record of the run handle is written by
        # `_async_submission_result`.
        if body.get("findall_id"):
            units["findall_id"] = body.get("findall_id")
        if isinstance(body.get("metadata"), Mapping):
            units["metadata"] = dict(body["metadata"])
        if units:
            metadata["provider_units"] = units
        return metadata


class FirecrawlProvider(HttpProvider):
    """Firecrawl scrape/crawl/extract with scrape-backed search.

    Wire format verified live 2026-09-19 against ``https://api.firecrawl.dev``:
    the current surface is ``v2``, not ``v1``. ``POST /v2/search`` returns
    results nested under ``data.web`` -- a shape the generic extractor reads as
    a single opaque record -- and returns only url/title/description unless
    ``scrapeOptions.formats`` asks for page content. Spend is reported as
    ``creditsUsed``.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "firecrawl",
            capabilities=ProviderCapabilities(
                provider_id="firecrawl",
                operations=frozenset({"search", "fetch", "crawl", "extract", "health"}),
                requires_credential=True,
                cost_observable=False,
                units_observable=True,
                domain_surfaces=frozenset({"firecrawl.category.developer"}),
                notes=("scrape-backed search plus first-class crawl and extract",),
            ),
            base_url="https://api.firecrawl.dev",
            endpoints={
                "search": "/v2/search",
                "fetch": "/v2/scrape",
                "crawl": "/v2/crawl",
                "extract": "/v2/extract",
            },
            **kwargs,
        )

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        options = dict(request.options)
        num_results = options.pop("num_results", None)
        # Firecrawl search exposes neither a retrieval-depth selector nor a
        # per-result character cap, so both knobs are dropped rather than
        # forwarded as unknown fields the API would reject.
        options.pop("mode", None)
        options.pop("max_chars_per_result", None)
        scrape_options: dict[str, Any] = dict(options.pop("scrapeOptions", {}) or {})
        scrape_options.setdefault("formats", [{"type": "markdown"}])
        if request.operation == "search":
            body: dict[str, Any] = {"query": request.query, "scrapeOptions": scrape_options}
            if num_results is not None:
                body["limit"] = int(num_results)
            body.update(options)
            return body
        if request.operation == "fetch":
            body = {"url": request.url, **scrape_options}
            body.update(options)
            return body
        body = {"url": request.seed_url or request.url}
        if num_results is not None:
            body["limit"] = int(num_results)
        body.update(options)
        return body

    def _extract_items(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> tuple[Mapping[str, Any], ...]:
        # v2 nests results by source channel: {"data": {"web": [...], ...}}.
        # The generic extractor sees `data` as a Mapping and yields it whole as
        # one sourceless record, so unwrap the channels explicitly first.
        if isinstance(body, Mapping):
            data = body.get("data")
            if isinstance(data, Mapping):
                items: list[Mapping[str, Any]] = []
                found_search_channel = False
                for value in data.values():
                    if isinstance(value, list):
                        found_search_channel = True
                        items.extend(item for item in value if isinstance(item, Mapping))
                if found_search_channel:
                    return tuple(items)
        return extract_items(body)

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = dict(item)
        # Prefer scraped page content; fall back to the SERP description when a
        # page blocks scraping (common for Reddit/X), which is a real signal and
        # must not be silently recorded as an empty source.
        body_text = item.get("markdown") or item.get("summary") or item.get("description")
        if body_text:
            normalized["snippet"] = str(body_text)
        metadata = item.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("publishedTime", "modifiedTime", "article:published_time"):
                if metadata.get(key):
                    normalized.setdefault("published_at", metadata[key])
                    break
        return normalized

    def _cost_metadata(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> dict[str, Mapping[str, Any]]:
        metadata = dict(extract_cost_metadata(body))
        if not isinstance(body, Mapping):
            return metadata
        units = dict(metadata.get("provider_units", {}))
        if body.get("creditsUsed") is not None:
            units["credits_used"] = body.get("creditsUsed")
        if body.get("id"):
            units["request_id"] = body.get("id")
        if units:
            metadata["provider_units"] = units
        return metadata


_HTML_TAG_RE = re.compile(r"<[^>]+>")


class BraveProvider(HttpProvider):
    """Brave independent-index web search.

    Wire format verified live 2026-09-25 against ``https://api.search.brave.com``:
    ``/res/v1/web/search`` answers ``POST`` with a JSON body as well as the
    documented ``GET`` with query parameters, with an identical response, so
    the shared POST transport serves it unchanged. It authenticates with
    ``X-Subscription-Token`` and takes ``{q, count}``. Results nest under
    ``web.results``; ``description`` carries inline ``<strong>`` highlight
    markup, and ``extra_snippets`` (opt-in) adds up to five further excerpts.
    ``page_age`` is the page's ISO timestamp. Quota is reported only in the
    ``x-ratelimit-*`` headers, never the body, so neither spend nor units are
    observable from the payload.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "brave",
            capabilities=ProviderCapabilities(
                provider_id="brave",
                operations=frozenset({"search", "health"}),
                requires_credential=True,
                cost_observable=False,
                units_observable=False,
                domain_surfaces=frozenset(),
                notes=("independent web index; ranked snippets with optional extra excerpts",),
            ),
            base_url="https://api.search.brave.com",
            endpoints={"search": "/res/v1/web/search"},
            **kwargs,
        )

    def _headers(self, credential: str) -> Mapping[str, str]:
        return {"X-Subscription-Token": credential, "Accept": "application/json"}

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        options = dict(request.options)
        num_results = options.pop("num_results", None)
        # Brave exposes neither a retrieval-depth selector nor a per-result
        # character cap on web search; both knobs are dropped rather than
        # forwarded as parameters the API does not define.
        options.pop("mode", None)
        options.pop("max_chars_per_result", None)
        options.pop("content_view", None)
        body: dict[str, Any] = {"q": request.query}
        if num_results is not None:
            # The API caps `count` at 20 per page.
            body["count"] = max(1, min(20, int(num_results)))
        # Extra snippets are the closest Brave gets to a query-relevant excerpt
        # view; without them each result carries one ~200-char description.
        body.setdefault("extra_snippets", True)
        body.update(options)
        return body

    def _extract_items(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> tuple[Mapping[str, Any], ...]:
        # {"web": {"results": [...]}} -- the generic extractor would read `web`
        # as nothing and return no sources at all.
        if isinstance(body, Mapping):
            web = body.get("web")
            if isinstance(web, Mapping) and isinstance(web.get("results"), list):
                return tuple(item for item in web["results"] if isinstance(item, Mapping))
        return extract_items(body)

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = dict(item)
        parts = [str(item.get("description") or "")]
        extra = item.get("extra_snippets")
        if isinstance(extra, list):
            parts.extend(str(snippet) for snippet in extra if snippet)
        # Strip the highlight markup: it is presentation, and leaving it in
        # would count `<strong>` tags as retrieved content in content_length.
        text = " ".join(_HTML_TAG_RE.sub("", part).strip() for part in parts if part)
        if text:
            normalized["snippet"] = text
        if item.get("page_age"):
            normalized.setdefault("published_at", item["page_age"])
        return normalized


class TavilyProvider(HttpProvider):
    """Tavily search and extraction.

    Wire format verified live 2026-09-25 against ``https://api.tavily.com``:
    ``POST /search`` authenticates with ``Authorization: Bearer tvly-...`` and
    takes ``{query, max_results, search_depth}``; results are a flat
    ``results`` list of ``{url, title, content, score, raw_content}``.
    ``POST /extract`` takes ``{urls: [...]}`` and returns ``raw_content`` per
    URL plus a ``failed_results`` list. Both report spend as
    ``usage.credits`` when ``include_usage`` is set.
    """

    DEFAULT_SEARCH_DEPTH = "basic"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "tavily",
            capabilities=ProviderCapabilities(
                provider_id="tavily",
                operations=frozenset({"search", "fetch", "health"}),
                requires_credential=True,
                cost_observable=False,
                units_observable=True,
                domain_surfaces=frozenset(),
                notes=("LLM-oriented search with query-relevant content; extract for fetch",),
            ),
            base_url="https://api.tavily.com",
            endpoints={"search": "/search", "fetch": "/extract"},
            **kwargs,
        )

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        options = dict(request.options)
        num_results = options.pop("num_results", None)
        # `mode` maps onto Tavily's own depth selector: `fast` is the
        # latency-matched `basic`, anything deeper is `advanced`. An explicit
        # `search_depth` option wins, so a suite can pin Tavily's native value.
        mode = options.pop("mode", None)
        options.pop("max_chars_per_result", None)
        options.pop("content_view", None)
        if request.operation == "fetch":
            body: dict[str, Any] = {"urls": [request.url], "include_usage": True}
            body.update(options)
            return body
        depth = options.pop("search_depth", None) or (
            "basic" if mode in (None, "fast") else "advanced"
        )
        body = {"query": request.query, "search_depth": depth, "include_usage": True}
        if num_results is not None:
            body["max_results"] = max(1, min(20, int(num_results)))
        body.update(options)
        return body

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = dict(item)
        # Search returns a query-relevant `content` excerpt; extract returns the
        # page as `raw_content`. Prefer the excerpt so a search source is not
        # silently replaced by a whole page the caller did not ask for.
        body_text = item.get("content") or item.get("raw_content")
        if body_text:
            normalized["snippet"] = str(body_text)
        if item.get("published_date"):
            normalized.setdefault("published_at", item["published_date"])
        return normalized

    def _cost_metadata(
        self, body: Mapping[str, Any] | list[Any] | str | None
    ) -> dict[str, Mapping[str, Any]]:
        metadata = dict(extract_cost_metadata(body))
        if not isinstance(body, Mapping):
            return metadata
        units = dict(metadata.get("provider_units", {}))
        usage = body.get("usage")
        if isinstance(usage, Mapping) and usage.get("credits") is not None:
            units["credits_used"] = usage.get("credits")
        if body.get("request_id"):
            units["request_id"] = body.get("request_id")
        if body.get("response_time") is not None:
            units["response_time_seconds"] = body.get("response_time")
        if units:
            metadata["provider_units"] = units
        return metadata


class PerplexityProvider(HttpProvider):
    """Perplexity Search API: raw ranked results, no LLM answer.

    Wire format per docs.perplexity.ai/api-reference/search-post, verified
    live 2026-09-25: ``POST /search`` with Bearer auth takes ``{query,
    max_results, search_type}`` and returns a flat ``results`` list of
    ``{title, url, snippet, date, last_updated}`` plus a top-level ``id``.
    ``search_type`` is ``web`` (standard) or ``fast`` (lower latency);
    ``max_results`` tops out at 20 for web search. Page content is sized by
    ``search_context_size`` or, when a per-page budget is given,
    ``max_tokens_per_page`` -- the docs say not to send both. Billing is a flat
    $5 (web) / $1 (fast) per 1,000 requests with no token charges, but the
    response reports no spend, so none is recorded here.

    This is the retrieval peer of the other search adapters. Perplexity's
    Agent API -- web-grounded structured answers -- is the agent lane's
    ``perplexity-agent`` arm, not this adapter.
    """

    # The docs' own conversion basis is tokens; the lane's cross-vendor knob
    # is characters. 4 chars/token is the approximation the agent lane already
    # labels as an estimate (CHARS_PER_TOKEN).
    CHARS_PER_TOKEN = 4

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "perplexity",
            capabilities=ProviderCapabilities(
                provider_id="perplexity",
                operations=frozenset({"search", "health"}),
                requires_credential=True,
                cost_observable=False,
                units_observable=False,
                domain_surfaces=frozenset(),
                notes=("ranked web results with query-relevant page content",),
            ),
            base_url="https://api.perplexity.ai",
            endpoints={"search": "/search"},
            **kwargs,
        )

    def _request_body(self, request: ProviderRequest) -> dict[str, Any]:
        options = dict(request.options)
        num_results = options.pop("num_results", None)
        mode = options.pop("mode", None)
        max_chars = options.pop("max_chars_per_result", None)
        options.pop("content_view", None)
        body: dict[str, Any] = {
            "query": request.query,
            # `fast` is the documented low-latency search type; anything else
            # is standard web search. An explicit `search_type` option wins.
            "search_type": options.pop("search_type", None)
            or ("fast" if mode == "fast" else "web"),
        }
        if num_results is not None:
            body["max_results"] = max(1, min(20, int(num_results)))
        if max_chars is not None and "search_context_size" not in options:
            body["max_tokens_per_page"] = max(1, int(max_chars) // self.CHARS_PER_TOKEN)
        body.update(options)
        return body

    def _normalize_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = dict(item)
        if item.get("date"):
            normalized.setdefault("published_at", item["date"])
        if item.get("last_updated"):
            normalized.setdefault("modified_at", item["last_updated"])
        return normalized


def make_not_applicable_result(
    *,
    provider_id: str,
    request: ProviderRequest,
    started_at: str,
    capabilities: ProviderCapabilities,
    error_class: str,
) -> ProviderResult:
    ended = utc_now()
    call_record = make_call_record(
        provider_id=provider_id,
        request=request,
        started_at=started_at,
        ended_at=ended,
        status="not_applicable",
        response={"capabilities": capabilities.as_metadata()},
        sources=(),
        error_class=error_class,
    )
    return ProviderResult(
        provider_id=provider_id,
        operation=request.operation,
        status="not_applicable",
        started_at=started_at,
        ended_at=ended,
        retry_count=0,
        call_record=call_record,
        error_class=error_class,
    )


def make_call_record(
    *,
    provider_id: str,
    request: ProviderRequest,
    started_at: str,
    ended_at: str,
    status: str,
    response: Mapping[str, Any],
    sources: tuple[dict[str, Any], ...],
    error_class: str | None,
    error_detail: str | None = None,
    call_id: str | None = None,
) -> dict[str, Any]:
    sanitized_response = dict(response)
    if error_detail:
        sanitized_response["error_detail"] = error_detail
    return validate_provider_call(
        {
            "schema_version": SCHEMA_VERSION,
            "call_id": call_id or stable_call_id(provider_id, request, started_at),
            "run_id": request.run_id,
            "provider_id": provider_id,
            "operation": request.operation,
            "status": SCHEMA_STATUS_BY_RESULT_STATUS[status],
            "started_at": started_at,
            "ended_at": ended_at,
            "request": sanitize_request(request),
            "response": sanitized_response,
            "normalized_source_refs": [f"sources/{source['source_id']}.json" for source in sources],
            # Stamped by `_call_domain_surface` when a logical call issued more
            # than one round trip; see ProviderResult.extra_call_records.
            "retry_count": 0,
            **({"error_class": error_class} if error_class else {}),
        }
    )


def sanitize_request(request: ProviderRequest) -> dict[str, Any]:
    data: dict[str, Any] = {"operation": request.operation, "redacted": True}
    if request.query is not None:
        data["query_length"] = len(request.query)
    if request.url is not None:
        data["url"] = request.url
    if request.seed_url is not None:
        data["seed_url"] = request.seed_url
    if request.options:
        data["option_keys"] = sorted(str(key) for key in request.options)
    return data


def make_source(provider_id: str, source_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or item.get("link") or "fixture://missing-url")
    title = str(item.get("title") or item.get("name") or url)
    full_text = str(
        item.get("snippet") or item.get("excerpt") or item.get("text") or item.get("content") or ""
    )
    final_snippet = full_text[:1200] or title
    # content_length measures what the PROVIDER returned, not what we retained.
    # The 1200-char snippet cap is a retention policy for evidence bundles; if
    # content_length were derived from the truncated snippet instead, every
    # provider would report an identical ~1200 and the context-cost comparison
    # -- the whole point of measuring token consumption -- would read as a tie.
    content_length = len((full_text or title).encode("utf-8"))
    source = {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "provider_id": provider_id,
        "url": url,
        "title": title,
        "snippet": final_snippet,
        "published_at": optional_str(item.get("published_at") or item.get("publishedDate")),
        "modified_at": optional_str(item.get("modified_at") or item.get("modifiedDate")),
        "retrieved_at": utc_now(),
        "content_hash": content_hash(final_snippet),
        "content_length": content_length,
        "redaction": {"state": "sanitized_excerpt", "raw_content_included": False},
    }
    return validate_normalized_source(source)


def extract_items(
    body: Mapping[str, Any] | list[Any] | str | None,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(body, list):
        return tuple(item for item in body if isinstance(item, Mapping))
    if not isinstance(body, Mapping):
        return ()
    for key in ("results", "data", "sources", "documents"):
        value = body.get(key)
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, Mapping))
        if isinstance(value, Mapping):
            return (value,)
    return ()


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (TimeoutError, socket.timeout))
    return False


def http_error_body(exc: urllib.error.HTTPError) -> dict[str, str]:
    try:
        raw = exc.read(4096)
    except OSError:
        return {}
    if not raw:
        return {}
    return {"error_body": raw.decode("utf-8", errors="replace")[:1024]}


def extract_cost_metadata(
    body: Mapping[str, Any] | list[Any] | str | None,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(body, Mapping):
        return {}
    metadata: dict[str, Mapping[str, Any]] = {}
    units = body.get("usage") or body.get("units")
    if isinstance(units, Mapping):
        metadata["provider_units"] = dict(units)
    cost = body.get("cost")
    if isinstance(cost, Mapping):
        metadata["provider_cost"] = dict(cost)
    return metadata


def provider_result_cost_metadata(
    body: Mapping[str, Any] | list[Any] | str | None,
) -> dict[str, Mapping[str, Any]]:
    metadata = dict(extract_cost_metadata(body))
    if isinstance(body, Mapping):
        for key in ("provider_units", "provider_cost"):
            value = body.get(key)
            if isinstance(value, Mapping):
                metadata[key] = dict(value)
    return metadata


def _replace_call_record(
    record: Any, *, response_updates: Mapping[str, Any] | None = None, **fields: Any
) -> Any:
    """Copy a call record with updates, rather than editing one in place.

    The frozen `ProviderResult` around this dict is rebuilt with
    `dataclasses.replace`; leaving the dict shared would make that immutability
    cosmetic, since the caller still holding the old result would see its
    record change underneath it. Non-dict records (and a missing `response`)
    pass through untouched.
    """
    if not isinstance(record, dict):
        return record
    copied = dict(record)
    for key, value in fields.items():
        if value is not None:
            copied[key] = value
    if response_updates:
        response = copied.get("response")
        if isinstance(response, dict):
            copied["response"] = {**response, **dict(response_updates)}
    return copied


def classify_http_status(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "ok"
    if status_code == 429:
        return "rate_limited"
    if status_code in {401, 403, 451}:
        return "refused"
    if status_code in {408, 504}:
        return "timeout"
    return "failed"


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_call_id(provider_id: str, request: ProviderRequest, started_at: str) -> str:
    seed = json.dumps(
        {
            "provider_id": provider_id,
            "run_id": request.run_id,
            "operation": request.operation,
            "query": request.query,
            "url": request.url,
            "seed_url": request.seed_url,
            "options": request.options,
            "started_at": started_at,
        },
        sort_keys=True,
    )
    return f"{provider_id}-{request.operation}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
