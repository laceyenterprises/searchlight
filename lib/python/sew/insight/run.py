"""Execute an insight run: every selected question on every arm, under a spend ledger.

A cell is one question on one arm. It sends the profile's exact body (polling
async research runs to completion, stopping one at its frozen timeout, and
running fetch post-steps), records the request, the raw response, cost and
latency, and writes one file. A rerun skips cells already ``ok`` or
deterministically ``failed``. Credentials come only from ``CredentialResolver``;
a resolved key fills the profile's header slot and is scrubbed from every file.

Before any arm runs, the shared query-writer runs once per question when a
selected profile asks for ``{{OBJECTIVE}}`` or ``{{KEYWORD_QUERIES}}``, and its
output is stored with the run.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode, urlsplit

from ..async_poll import _ASYNC_RUN_HANDLE_RE
from ..backoff import bounded_exponential_delay
from ..meter_pricing import measured_spend
from ..providers import (
    CredentialResolver,
    CredentialUnavailable,
    HttpResponse,
    HttpTransport,
    UrllibTransport,
    classify_http_status,
    http_error_body,
    is_timeout_error,
    make_provider,
)
from ..state import default_state_root
from .catalog import _KEY_SLOT, InsightError, InsightSet, Profile, Shared
from .catalog import check_sponsor, load_profiles, load_set, load_shared, placeholders, render, request_body

RUN_VERSION = "insight-run-v2"
ITEM_CHARS = 6000  # per-result returned text kept for extraction, the same for every arm
PAGE_CHARS = 20000  # per-page text from a fetch post-step
RAW_CHARS = 1_000_000
RETRY_CODES = frozenset({429, 500, 502, 503, 504})
COMPLETE = frozenset({"ok", "failed"})  # a rerun skips these; anything else is retried
Model = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False, default=str),
                   encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def runs_root(state_root: Path | None = None) -> Path:
    return (state_root or default_state_root()) / "insight" / "runs"


def resolve_run(run: str, state_root: Path | None = None) -> Path:
    path = Path(run)
    if not (path / "manifest.json").is_file():
        path = runs_root(state_root) / run
    if not (path / "manifest.json").is_file():
        raise InsightError(f"no insight run {run!r} (looked for {path}/manifest.json)")
    return path


def cell_path(run_dir: Path, qid: str, arm_id: str) -> Path:
    return run_dir / "cells" / qid / f"{arm_id}.json"


# --------------------------------------------------------------------------
# Spend ledger
# --------------------------------------------------------------------------


class Ledger:
    """Hard caps: a cell runs only if its estimate fits under both the arm and run cap.

    Spend persists in the run directory, so caps hold across resumes. A cell's
    estimate is reserved before the call and replaced by its recorded cost after.
    """

    def __init__(self, path: Path, *, max_usd: float, arm_caps: Mapping[str, float]) -> None:
        self.path, self.max_usd, self.arm_caps = path, float(max_usd), dict(arm_caps)
        self._lock = threading.Lock()
        data = read_json(path) if path.is_file() else {}
        self.spent: dict[str, float] = {k: float(v) for k, v in data.get("spent", {}).items()}
        self.reserved: dict[str, float] = {}

    def total(self) -> float:
        return sum(self.spent.values()) + sum(self.reserved.values())

    def reserve(self, arm: str, usd: float) -> bool:
        with self._lock:
            arm_total = self.spent.get(arm, 0.0) + self.reserved.get(arm, 0.0) + usd
            if self.total() + usd > self.max_usd + 1e-9:
                return False
            if arm_total > self.arm_caps.get(arm, self.max_usd) + 1e-9:
                return False
            self.reserved[arm] = self.reserved.get(arm, 0.0) + usd
            return True

    def settle(self, arm: str, reserved: float, usd: float) -> None:
        with self._lock:
            self.reserved[arm] = max(0.0, self.reserved.get(arm, 0.0) - reserved)
            self.spent[arm] = self.spent.get(arm, 0.0) + usd
            write_json(self.path, {"spent": self.spent, "max_usd": self.max_usd,
                                   "arm_caps": self.arm_caps})


# --------------------------------------------------------------------------
# One cell
# --------------------------------------------------------------------------


def _query_string(body: Mapping[str, Any]) -> str:
    flat = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in body.items()}
    return urlencode(flat, doseq=True)


def _send(transport, method, url, headers, body, *, timeout, sleep, retries=3):
    """One request with bounded backoff on 429/5xx and transport errors."""
    if method == "GET" and body:
        url, body = f"{url}{'&' if '?' in url else '?'}{_query_string(body)}", None
    response, error = None, None
    for attempt in range(retries + 1):
        try:
            response, error = transport.request(
                method, url, headers=headers, json_body=body, timeout_seconds=timeout
            ), None
        except urllib.error.HTTPError as exc:
            response, error = HttpResponse(exc.code, http_error_body(exc)), None
        except OSError as exc:
            response, error = None, ("timeout" if is_timeout_error(exc) else "transport_error")
        if response is not None and response.status_code not in RETRY_CODES:
            return response, None
        if attempt < retries:
            sleep(bounded_exponential_delay(attempt, base_seconds=4.0, max_seconds=30.0))
    return response, error


def _field(body: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        body = body.get(part) if isinstance(body, Mapping) else None
    return body


def _poll(profile, first, transport, headers, deadline, sleep, clock):
    """Poll an accepted run. Returns (final response, error class, stopped at timeout)."""
    poll = profile.poll
    state = _field(first.body, "status")
    names = list(dict.fromkeys([poll["id_name"], "id", "run_id", "request_id"]))
    handle = next((_field(first.body, n) for n in names if _field(first.body, n)), None)
    response = first

    def url(template: str) -> str:
        return template.replace("{" + poll["id_name"] + "}", str(handle))

    if state not in poll["done"]:
        if not isinstance(handle, str) or not _ASYNC_RUN_HANDLE_RE.fullmatch(handle):
            return first, "async_run_handle_missing", False
        while state not in poll["done"]:
            if clock() >= deadline:
                if not poll.get("stop"):
                    return response, "poll_timeout", False
                # The frozen budget is spent: stop the run and keep what it gathered.
                method, stop_url = poll["stop"]
                _send(transport, method, url(stop_url), headers, {}, timeout=60.0, sleep=sleep)
                got, _ = _send(transport, "GET", url(poll["status_url"]), headers, None,
                               timeout=60.0, sleep=sleep)
                return (got if got is not None and got.status_code < 300 else response), None, True
            sleep(poll["interval_seconds"])
            got, _ = _send(transport, "GET", url(poll["status_url"]), headers, None,
                           timeout=60.0, sleep=sleep)
            if got is None or got.status_code >= 300:
                if got is not None and got.status_code < 500:
                    return got, f"poll_http_{got.status_code}", False
                continue
            response, state = got, _field(got.body, "status")
            if state in poll["failed"]:
                return response, f"run_{state}", False
    if poll.get("result_url") and handle:
        got, error = _send(transport, "GET", url(poll["result_url"]), headers, None,
                           timeout=120.0, sleep=sleep)
        if got is None or got.status_code >= 300:
            return got or first, error or f"result_http_{getattr(got, 'status_code', 'none')}", False
        return got, None, False
    return response, None, False


def _walk_items(body: Any) -> list[Mapping[str, Any]]:
    """Fallback for response shapes no adapter knows: any object with a web URL."""
    found: list[Mapping[str, Any]] = []
    stack = [body]
    while stack:
        node = stack.pop(0)
        if isinstance(node, Mapping):
            if str(node.get("url") or "").startswith("http"):
                found.append(node)
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


_TEXT_KEYS = ("snippet", "text", "content", "markdown", "full_content", "raw_content",
              "snippets", "excerpts", "description")
# A fetched page: the body before any excerpt view an adapter put in `snippet`.
_PAGE_KEYS = ("full_content", "raw_content", "markdown", "text", "content", *_TEXT_KEYS)


def _normalized(profile: Profile, body: Any) -> list[Mapping[str, Any]]:
    """Response records through the vendor adapter's own parser, else a generic walk."""
    adapter = make_provider("brave" if profile.credential == "brave-answers" else profile.credential)
    raw = [adapter._normalize_item(i) for i in adapter._extract_items(body)]
    if not any(str(i.get("url") or "").startswith("http") for i in raw):
        raw += [adapter._normalize_item(i) for i in _walk_items(body)]
    return raw


def _text(item: Mapping[str, Any], keys: tuple[str, ...] = _TEXT_KEYS) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            value = "\n\n".join(str(v) for v in value if v)
        if value:
            return str(value)
    return ""


def items_from(profile: Profile, body: Any) -> list[dict[str, Any]]:
    """The first ``top_k`` unique result URLs in returned order, at a common text budget."""
    items: dict[str, dict[str, Any]] = {}
    for item in _normalized(profile, body):
        url = str(item.get("url") or item.get("link") or "")
        if not url.startswith("http"):
            continue
        text = _text(item)
        if url in items:  # a multi-query or multi-section response repeats URLs
            if len(text) > items[url]["chars_delivered"]:
                items[url].update(text=text[:ITEM_CHARS], chars_delivered=len(text))
            continue
        items[url] = {"url": url, "title": str(item.get("title") or "")[:300],
                      "published": item.get("published_at") or item.get("publishedDate"),
                      "text": text[:ITEM_CHARS], "chars_delivered": len(text)}
    return list(items.values())[: profile.top_k]


def _skip(url: str, domains: Iterable[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


_STREAMED_COST = re.compile(r'\\?"X-Request-Total-Cost\\?": ?([0-9.]+)')


def _cost(profile: Profile, bodies: list[Any], billed_calls: int) -> dict[str, Any]:
    usd, credits, measured = 0.0, 0.0, 0
    for body in bodies:
        # Brave Answers streams its bill in a <usage> block (X-Request-Total-Cost, USD).
        streamed = _STREAMED_COST.findall(body) if isinstance(body, str) else []
        if streamed:
            usd += float(streamed[-1])
            measured += 1
            continue
        spend = measured_spend([body])
        if "provider_cost" in spend:
            usd += float(spend["provider_cost"]["total"])
            measured += 1
        elif "provider_units" in spend and profile.usd_per_credit is not None:
            credits += float(spend["provider_units"]["credits_used"])
            measured += 1
    usd += credits * (profile.usd_per_credit or 0.0)
    if measured < billed_calls:
        # Any unpriced call makes the whole cell an estimate; never below the profile's.
        return {"usd": max(usd, profile.est_cost_usd), "basis": "estimate", "credits": credits}
    return {"usd": usd, "basis": "measured", "credits": credits}


def _post_steps(profile, items, values, transport, headers, sleep, cost_bodies) -> int:
    """Fetch the top results' pages: one batch call for {{TOP_K_URLS}}, else one per {{URL}}."""
    calls = 0
    for step in profile.post_steps:
        method, _, url = step["endpoint"].strip().rpartition(" ")
        targets = [i for i in items[: int(step.get("top_k") or 3)]
                   if not _skip(i["url"], step.get("skip_domains") or ())]
        batched = "{{TOP_K_URLS}}" in json.dumps(step["params"])
        for batch in ([targets] if batched else [[t] for t in targets]):
            if not batch:
                continue
            body = render(step["params"], {**values, "TOP_K_URLS": [t["url"] for t in batch],
                                           "URL": batch[0]["url"]})
            got, error = _send(transport, method or "POST", url, headers, body,
                               timeout=120.0, sleep=sleep)
            calls += 1
            if got is None or got.status_code >= 300:
                for t in batch:
                    t["post_step"] = f"fetch_failed_{getattr(got, 'status_code', error)}"
                continue
            cost_bodies.append(got.body)
            fetched = _normalized(profile, got.body)
            pages = {str(p.get("url") or "").rstrip("/"): _text(p, _PAGE_KEYS) for p in fetched}
            for t in batch:
                text = pages.get(t["url"].rstrip("/"))
                if text is None and len(batch) == 1 and fetched:
                    text = _text(fetched[0], _PAGE_KEYS)  # a single-page response may omit the URL
                t["post_step"] = "fetched" if text else "fetch_empty"
                if text:
                    t["page_text"], t["page_chars_delivered"] = text[:PAGE_CHARS], len(text)
    return calls


def _headers(profile: Profile, secret: str) -> dict[str, str]:
    raw = profile.headers or {"Authorization": "Bearer <KEY>"}
    return {k: _KEY_SLOT.sub(lambda _: secret, str(v)) for k, v in raw.items()}


def execute_cell(
    profile: Profile,
    question: Mapping[str, Any],
    *,
    values: Mapping[str, Any],
    transport: HttpTransport,
    resolver: CredentialResolver,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    body = request_body(profile, question, values)
    record: dict[str, Any] = {
        "version": RUN_VERSION, "arm_id": profile.arm_id, "question_id": question["id"],
        "profile_sha256": profile.sha256, "date": datetime.now(UTC).date().isoformat(),
        "started_at": now(),
        "request": {"method": profile.method, "url": profile.endpoint, "headers": profile.headers,
                    "body": body},
        "cost": {"usd": 0.0, "basis": "none", "credits": 0.0},
    }
    try:
        secret, _ = resolver.resolve(profile.credential)
    except CredentialUnavailable as exc:
        return {**record, "status": "unavailable", "error_class": "missing_credential",
                "detail": str(exc)[:200]}
    headers = _headers(profile, secret)
    t0 = clock()
    response, error = _send(transport, profile.method, profile.endpoint, headers, body,
                            timeout=180.0 if profile.poll else profile.timeout_seconds, sleep=sleep,
                            retries=0 if profile.poll and profile.method == "POST" else 3)
    status = classify_http_status(response.status_code) if response is not None else error
    # A timed-out/5xx task submission may already have created a paid run.
    uncertain_submission = bool(profile.poll and profile.method == "POST" and (
        response is None or response.status_code >= 500
    ))
    accepted, stopped = status == "ok" or uncertain_submission, False
    if status == "ok" and profile.poll:
        response, error, stopped = _poll(profile, response, transport, headers,
                                         t0 + profile.timeout_seconds, sleep, clock)
        status = "timeout" if error == "poll_timeout" else ("failed" if error else "ok")
    final = response.body if response is not None else None
    cost_bodies, billed, items = [final], 1, []
    if status == "ok" and profile.kind == "results":
        items = items_from(profile, final)
        billed += _post_steps(profile, items, values, transport, headers, sleep, cost_bodies)
    notices = final.get("notices") if isinstance(final, Mapping) else None
    raw = json.dumps(final, default=str)
    record.update(
        status=status,
        error_class=None if status == "ok" else (error or f"http_{response.status_code}"),
        http_status=response.status_code if response is not None else None,
        latency_ms=round((clock() - t0) * 1000.0, 1), ended_at=now(), items=items,
        notices=notices or None, stopped_at_timeout=stopped,
        response=final if len(raw) <= RAW_CHARS else {"_truncated": raw[:RAW_CHARS]},
    )
    if accepted:
        record["cost"] = _cost(profile, cost_bodies, billed)
    text = json.dumps(record, default=str)
    if secret and secret in text:
        record = json.loads(text.replace(secret, "<redacted:credential>"))
    return record


# --------------------------------------------------------------------------
# The shared query-writer
# --------------------------------------------------------------------------


def write_queries(model: Model, shared: Shared, question: Mapping[str, Any]) -> dict[str, Any]:
    """One objective and 2-3 keyword queries, for the vendors whose docs prescribe them."""
    payload = {
        "instructions": shared.writer_prompt.replace("{{QUERY}}", str(question["text"])),
        "response_contract": {"objective": "<string>", "keyword_queries": ["<string>"]},
    }
    error: Exception | None = None
    for _ in range(2):
        try:
            reply = model(copy.deepcopy(payload))
            objective = str(reply["objective"]).strip()
            queries = [str(q).strip()[:200] for q in reply["keyword_queries"] if str(q).strip()][:3]
            if objective and queries:
                return {"objective": objective[:5000], "keyword_queries": queries}
            error = ValueError("empty objective or keyword_queries")
        except (KeyError, TypeError, ValueError) as exc:
            error = exc
    raise InsightError(f"query-writer reply unusable for {question['id']}: {error}")


# --------------------------------------------------------------------------
# A run
# --------------------------------------------------------------------------


def _manifest(run_dir, iset: InsightSet, shared: Shared, profiles, qids, run_date, extractor_model):
    path = run_dir / "manifest.json"
    if path.is_file():
        manifest = read_json(path)
        if manifest["set_sha256"] != iset.sha256:
            raise InsightError(f"set {iset.set_id} changed since run {run_dir.name} began")
        if manifest["shared_sha256"] != shared.sha256:
            raise InsightError(f"_shared.json changed since run {run_dir.name} began; start a new run")
        if extractor_model and manifest.get("extractor_model") not in (None, extractor_model):
            raise InsightError(f"run {run_dir.name} pinned extractor {manifest['extractor_model']}")
        for arm_id, profile in profiles.items():
            frozen = manifest["profiles"].get(arm_id)
            if frozen and frozen["sha256"] != profile.sha256:
                raise InsightError(f"profile {arm_id} changed since run {run_dir.name} began; "
                                   "start a new run")
    else:
        manifest = {"version": RUN_VERSION, "run_id": run_dir.name, "set_id": iset.set_id,
                    "set_sha256": iset.sha256, "shared_sha256": shared.sha256,
                    "shared": shared.raw, "run_date": run_date.isoformat(), "created_at": now(),
                    "questions": [], "profiles": {},
                    "bench": {"item_chars": ITEM_CHARS, "page_chars": PAGE_CHARS}}
    manifest["extractor_model"] = manifest.get("extractor_model") or extractor_model
    manifest["questions"] = sorted(set(manifest["questions"]) | set(qids))
    for arm_id, profile in profiles.items():
        manifest["profiles"].setdefault(arm_id, {"sha256": profile.sha256, "profile": profile.raw,
                                                 "unverified": profile.unverified})
    return manifest


def plan_run(
    set_id: str,
    *,
    arms: list[str] | None = None,
    question_ids: list[str] | None = None,
    max_usd: float = 25.0,
    arm_max_usd: float | None = None,
    run_id: str | None = None,
    state_root: Path | None = None,
    today: date | None = None,
    extractor_model: str | None = None,
    set_root: Path | None = None,
    profile_dir: Path | None = None,
) -> dict[str, Any]:
    """Everything a run would do, without touching the network or the disk."""
    iset = load_set(set_id, set_root)
    shared = load_shared(profile_dir)
    every = load_profiles(arms, directory=profile_dir)
    profiles = {k: p for k, p in every.items() if p.runnable}
    check_sponsor(iset, profiles)
    qids = question_ids or [q["id"] for q in iset.questions]
    for qid in qids:
        iset.question(qid)
    today = today or datetime.now(UTC).date()
    run_id = run_id or f"{set_id}-{today:%Y%m%d}"
    run_dir = runs_root(state_root) / run_id
    cells = []
    for arm_id, profile in profiles.items():
        for qid in qids:
            path = cell_path(run_dir, qid, arm_id)
            done = path.is_file() and read_json(path).get("status") in COMPLETE
            cells.append({"question_id": qid, "arm_id": arm_id, "done": done,
                          "est_usd": 0.0 if done else profile.est_cost_usd})
    est = {a: round(sum(c["est_usd"] for c in cells if c["arm_id"] == a), 4) for a in profiles}
    caps = {a: float(arm_max_usd) if arm_max_usd is not None
            else round(max(2 * profiles[a].est_cost_usd * len(qids), 0.05), 4) for a in profiles}
    needs_writer = any(p.needs_writer for p in profiles.values())
    return {
        "run_id": run_id, "run_dir": str(run_dir), "set_id": set_id, "set": iset, "shared": shared,
        "profiles": profiles, "questions": qids, "cells": cells, "today": today,
        "extractor_model": extractor_model,
        "est_usd_by_arm": est, "est_usd_total": round(sum(est.values()), 4),
        "max_usd": float(max_usd), "arm_caps_usd": caps,
        "not_runnable": {k: p.not_runnable for k, p in every.items() if not p.runnable},
        "unverified_fields": {k: p.unverified for k, p in profiles.items() if p.unverified},
        "query_writer_questions": [q for q in qids if needs_writer
                                   and not (run_dir / "query-writer" / f"{q}.json").is_file()],
    }


def dry_run_view(plan: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("run_id", "run_dir", "set_id", "questions", "est_usd_by_arm", "est_usd_total", "max_usd",
            "arm_caps_usd", "not_runnable", "unverified_fields", "query_writer_questions")
    todo = [c for c in plan["cells"] if not c["done"]]
    return {**{k: plan[k] for k in keys}, "fits_cap": plan["est_usd_total"] <= plan["max_usd"],
            "cells_planned": len(todo), "cells_done": len(plan["cells"]) - len(todo), "cells": todo}


def run(
    plan: Mapping[str, Any],
    *,
    writer: Callable[[], Model] | None = None,
    transport: HttpTransport | None = None,
    resolver: CredentialResolver | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = print,
) -> dict[str, Any]:
    run_dir = Path(plan["run_dir"])
    profiles: Mapping[str, Profile] = plan["profiles"]
    iset, shared = plan["set"], plan["shared"]
    if plan["est_usd_total"] > plan["max_usd"]:
        # Refuse rather than let whichever arms start first spend the budget:
        # a partial run would hand later arms fewer questions.
        raise InsightError(f"estimated ${plan['est_usd_total']:.2f} exceeds --max-usd "
                           f"{plan['max_usd']:.2f}; raise the cap or narrow --arms/--questions")
    if plan["query_writer_questions"] and writer is None:
        raise InsightError("these profiles need the shared query-writer; pin --extractor-model")
    manifest = _manifest(run_dir, iset, shared, profiles, plan["questions"], plan["today"],
                         plan["extractor_model"])
    write_json(run_dir / "manifest.json", manifest)
    run_date = date.fromisoformat(manifest["run_date"])
    if plan["query_writer_questions"]:
        model = writer()
        for qid in plan["query_writer_questions"]:
            out = write_queries(model, shared, iset.question(qid))
            write_json(run_dir / "query-writer" / f"{qid}.json", {
                **out, "question_id": qid, "shared_sha256": shared.sha256,
                "model": {"harness": getattr(model, "harness_id", None),
                          "model": getattr(model, "model_id", None) or manifest["extractor_model"]}})
    transport = transport or UrllibTransport()
    resolver = resolver or CredentialResolver()
    ledger = Ledger(run_dir / "ledger.json", max_usd=plan["max_usd"], arm_caps=plan["arm_caps_usd"])
    lock = threading.Lock()
    counts: dict[str, int] = {}

    def one(arm_id: str, qid: str) -> None:
        profile, question = profiles[arm_id], iset.question(qid)
        written = run_dir / "query-writer" / f"{qid}.json"
        values = placeholders(question, profile, shared, run_date,
                              writer=read_json(written) if written.is_file() else None,
                              extractor_model=manifest["extractor_model"])
        if not ledger.reserve(arm_id, profile.est_cost_usd):
            record = {"version": RUN_VERSION, "arm_id": arm_id, "question_id": qid,
                      "status": "skipped_cap", "error_class": "spend_cap",
                      "cost": {"usd": 0.0, "basis": "none"}}
        else:
            try:
                record = execute_cell(profile, question, values=values, transport=transport,
                                      resolver=resolver, sleep=sleep)
            except Exception as exc:  # noqa: BLE001 - one bad cell must not end the run
                record = {"version": RUN_VERSION, "arm_id": arm_id, "question_id": qid,
                          "status": "error", "error_class": f"runner_exception:{type(exc).__name__}",
                          "detail": str(exc)[:300],
                          "cost": {"usd": profile.est_cost_usd, "basis": "estimate"}}
            ledger.settle(arm_id, profile.est_cost_usd, float(record["cost"]["usd"]))
            if profile.pace_seconds:
                sleep(profile.pace_seconds)
        write_json(cell_path(run_dir, qid, arm_id), record)
        with lock:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        if progress:
            progress(f"[{arm_id:<24}] {qid:<6} {record['status']:<12} "
                     f"{record.get('latency_ms', 0):>9.0f}ms ${record['cost']['usd']:.4f}")

    def drive(arm_id: str) -> None:
        # Each arm walks the questions in set order; arms run side by side.
        todo = [c["question_id"] for c in plan["cells"] if c["arm_id"] == arm_id and not c["done"]]
        with ThreadPoolExecutor(max_workers=max(1, profiles[arm_id].concurrency)) as pool:
            list(pool.map(lambda qid: one(arm_id, qid), todo))

    threads = [threading.Thread(target=drive, args=(a,)) for a in profiles]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "status_counts": counts,
            "spent_usd": {k: round(v, 4) for k, v in ledger.spent.items()},
            "spent_usd_total": round(sum(ledger.spent.values()), 4)}
