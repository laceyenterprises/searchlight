"""Question sets and frozen arm profiles for the insight bench.

A question set is ``catalogs/insight/<set_id>.json``. A profile is
``config/insight/profiles/<arm>.<operation>[.<variant>].json``: the exact request
one vendor operation receives, with ``{{PLACEHOLDER}}`` slots for the question,
and ``_shared.json`` holds the shared research brief and query-writer prompt.
Everything is hashed into the run manifest, so a resumed run refuses a change.

Profiles are data: a new vendor product is a new file. A profile whose
endpoint is not an HTTPS API (a native harness arm) or whose key slot no
``CredentialResolver`` id serves loads as documented but not runnable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from ..catalog import module_root
from ..schema import SchemaError

SET_DIR = Path("catalogs/insight")
PROFILE_DIR = Path("config/insight/profiles")
# Key slot named in a profile header ("<EXA_API_KEY>") -> CredentialResolver id.
KEY_SLOTS = {
    "EXA_API_KEY": "exa",
    "PARALLEL_API_KEY": "parallel-web",
    "TAVILY_API_KEY": "tavily",
    "BRAVE_API_KEY": "brave",
    "BRAVE_ANSWERS_KEY": "brave-answers",
    "FIRECRAWL_API_KEY": "firecrawl",
    "PERPLEXITY_API_KEY": "perplexity",
}
# USD per reported credit when a profile's cost_model gives none: Tavily
# pay-as-you-go, Firecrawl at its highest plan rate (an upper bound).
CREDIT_USD = {"tavily": 0.008, "firecrawl": 0.005}
WRITER_SLOTS = frozenset({"OBJECTIVE", "KEYWORD_QUERIES"})
SUCCESS_STATES = frozenset({"completed", "succeeded", "success", "done"})
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)(\.\.\.)?\}\}")
_KEY_SLOT = re.compile(r"<([A-Z_]+)>")
_STOPWORDS = frozenset(
    """a an and are as at be been by can could do does did for from has have how if in into is
    it its of on or so such than that the their them then there these they this those to was
    were what when where which who whom why will with within without about across any each
    other our your you we us also both more most some very""".split()
)


class InsightError(SchemaError):
    """A question set, profile or run cannot be used as given."""


@dataclass(frozen=True)
class InsightSet:
    set_id: str
    title: str
    sha256: str
    context: str
    questions: tuple[Mapping[str, Any], ...]
    baseline: str | None = None

    def question(self, qid: str) -> Mapping[str, Any]:
        for question in self.questions:
            if question["id"] == qid:
                return question
        raise InsightError(f"set {self.set_id} has no question {qid}")


@dataclass(frozen=True)
class Shared:
    sha256: str
    research_brief: str
    extractor_brief: str
    writer_prompt: str
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    arm_id: str
    arm: str
    operation: str
    kind: str  # "results" (ranked pages) or "answer" (a vendor-written report)
    sha256: str
    raw: Mapping[str, Any]
    est_cost_usd: float
    runnable: bool = True
    not_runnable: str | None = None
    method: str = "POST"
    endpoint: str = ""
    credential: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    post_steps: tuple[Mapping[str, Any], ...] = ()
    poll: Mapping[str, Any] | None = None
    top_k: int = 10
    pace_seconds: float = 0.0
    timeout_seconds: float = 120.0
    concurrency: int = 1
    default_arm: bool = True
    variant: str | None = None

    @property
    def headers(self) -> Mapping[str, str]:
        return dict(self.raw.get("headers") or {})

    @property
    def usd_per_credit(self) -> float | None:
        model = self.raw.get("cost_model")
        value = model.get("usd_per_credit") if isinstance(model, Mapping) else None
        if isinstance(value, (int, float)):
            return float(value)
        return CREDIT_USD.get(self.credential or "")

    @property
    def needs_writer(self) -> bool:
        return bool(slots([self.params, list(self.post_steps)]) & WRITER_SLOTS)

    @property
    def unverified(self) -> list[str]:
        fields = self.raw.get("unverified_fields")
        names = sorted(fields) if isinstance(fields, Mapping) else list(fields or [])
        flag = self.raw.get("unverified")
        if isinstance(flag, list):
            names += [str(item) for item in flag]
        elif flag is True and not names:
            names.append("profile")
        return names


def digest(document: Any) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InsightError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InsightError(f"{path} must hold a JSON object")
    return value


def slots(value: Any) -> set[str]:
    return {m.group(1) for m in _PLACEHOLDER.finditer(json.dumps(value))}


def load_set(set_id: str, root: Path | None = None) -> InsightSet:
    path = (root or module_root()) / SET_DIR / f"{set_id}.json"
    doc = _read_json(path)
    if doc.get("set_id") != set_id:
        raise InsightError(f"{path}: set_id must equal the file name ({set_id})")
    context = doc.get("context_for_judges")
    if not isinstance(context, str) or not context.strip():
        raise InsightError(f"{path}: context_for_judges is required")
    questions = doc.get("questions")
    if not isinstance(questions, list) or not questions:
        raise InsightError(f"{path}: questions must be a non-empty list")
    seen: set[str] = set()
    for q in questions:
        qid = q.get("id") if isinstance(q, dict) else None
        if not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", qid) or qid in seen:
            raise InsightError(f"{path}: question ids must be unique short identifiers ({qid!r})")
        seen.add(qid)
        if not isinstance(q.get("text"), str) or not q["text"].strip():
            raise InsightError(f"{path}: question {qid} has no text")
        days = q.get("freshness_days")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days < 1):
            raise InsightError(f"{path}: question {qid} freshness_days must be a positive integer")
    baseline = None
    if doc.get("baseline"):
        baseline_path = (path.parent / str(doc["baseline"])).resolve()
        if path.parent.resolve() not in baseline_path.parents:
            raise InsightError(f"{path}: baseline must live under {SET_DIR}")
        try:
            baseline = baseline_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InsightError(f"cannot read baseline {baseline_path}: {exc}") from exc
    return InsightSet(set_id=set_id, title=str(doc.get("title") or set_id),
                      sha256=digest({"set": doc, "baseline": baseline}), context=context,
                      questions=tuple(questions), baseline=baseline)


def load_shared(directory: Path | None = None) -> Shared:
    path = (directory or module_root() / PROFILE_DIR) / "_shared.json"
    doc = _read_json(path)
    try:
        return Shared(sha256=digest(doc), research_brief=str(doc["research_brief_template"]),
                      extractor_brief=str(doc.get("extractor_brief_template") or ""),
                      writer_prompt=str(doc["query_writer"]["prompt"]), raw=doc)
    except (KeyError, TypeError) as exc:
        raise InsightError(f"{path}: needs research_brief_template and query_writer.prompt") from exc


def _endpoint(raw: Any) -> tuple[str, str] | None:
    """``"POST https://..."`` or a bare URL; None when it is not an HTTPS API."""
    text = str(raw or "").strip()
    method = "POST"
    if text.split(" ", 1)[0] in ("GET", "POST"):
        method, text = (part.strip() for part in text.split(" ", 1))
    return (method, text) if re.fullmatch(r"https://\S+", text) else None


def _poll(spec: Any, where: str) -> tuple[dict[str, Any] | None, float | None]:
    if not isinstance(spec, Mapping):
        return None, None
    timeout = spec.get("timeout_s")
    timeout = float(timeout) if isinstance(timeout, (int, float)) else None
    if not spec.get("poll_endpoint"):
        return None, timeout  # synchronous or one streamed response: read whole
    status = _endpoint(spec["poll_endpoint"])
    name = re.search(r"\{(\w+)\}", status[1]) if status else None
    if not name:
        raise InsightError(f"{where}: async.poll_endpoint must be an https URL with an {{id}} slot")
    terminal = list(spec.get("terminal_states") or ("completed", "failed", "cancelled"))
    done = [s for s in terminal if s in SUCCESS_STATES] or ["completed"]
    result = _endpoint(spec.get("result_endpoint"))
    stop = _endpoint(str(spec.get("on_timeout") or "").split(" (")[0])
    return {
        "status_url": status[1], "id_name": name.group(1),
        "result_url": result[1] if result else None,
        "stop": stop, "interval_seconds": float(spec.get("interval_s") or 10),
        "done": done, "failed": [s for s in terminal if s not in done] or ["failed", "cancelled"],
    }, timeout


def parse_profile(doc: Mapping[str, Any], where: str) -> Profile:
    arm, operation = doc.get("arm"), doc.get("operation")
    if not isinstance(arm, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", arm):
        raise InsightError(f"{where}: arm must be a short lowercase vendor name")
    if not isinstance(operation, str) or not re.fullmatch(r"[a-z][a-z-]{1,23}", operation):
        raise InsightError(f"{where}: operation must be a short lowercase name")
    variant = doc.get("variant")
    if variant is not None and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", str(variant)):
        raise InsightError(f"{where}: variant must be a short lowercase name")
    est = doc.get("est_cost_usd_per_question")
    if not isinstance(est, (int, float)) or isinstance(est, bool) or est < 0:
        raise InsightError(f"{where}: est_cost_usd_per_question must be a non-negative number")
    kind = str(doc.get("kind") or ("results" if operation == "search" else "answer"))
    if kind not in ("results", "answer"):
        raise InsightError(f"{where}: kind must be results or answer")
    base = dict(
        arm_id=".".join(filter(None, (arm, operation, variant))), arm=arm, operation=operation,
        kind=kind, sha256=digest(doc), raw=copy.deepcopy(dict(doc)), est_cost_usd=float(est),
        variant=variant,
        default_arm=doc.get("default_arm", not doc.get("optional") and variant is None) is not False,
    )
    endpoint = _endpoint(doc.get("endpoint"))
    key_slots = {m for v in (doc.get("headers") or {}).values() for m in _KEY_SLOT.findall(str(v))}
    credential = next((KEY_SLOTS[s] for s in sorted(key_slots) if s in KEY_SLOTS), None)
    if endpoint is None:
        return Profile(**base, runnable=False, not_runnable="not an HTTPS API (harness or SDK arm)")
    if credential is None:
        return Profile(**base, runnable=False,
                       not_runnable=f"no CredentialResolver id for key slot(s) {sorted(key_slots)}")
    params = doc.get("params")
    if not isinstance(params, dict) or not slots(params) & {"QUERY", "RESEARCH_BRIEF", "OBJECTIVE"}:
        raise InsightError(f"{where}: params must carry {{{{QUERY}}}} or {{{{RESEARCH_BRIEF}}}}")
    steps = doc.get("post_steps") or []
    for step in steps:
        if not isinstance(step, dict) or step.get("op") != "fetch" or not _endpoint(step.get("endpoint")):
            raise InsightError(f"{where}: post_steps support only {{op: fetch, endpoint: https URL}}")
        if not slots(step.get("params")) & {"TOP_K_URLS", "URL"}:
            raise InsightError(f"{where}: a fetch post_step needs {{{{TOP_K_URLS}}}} or {{{{URL}}}}")
    poll, timeout = _poll(doc.get("async"), where)
    return Profile(
        **base, method=endpoint[0], endpoint=endpoint[1], credential=credential, params=params,
        post_steps=tuple(steps), poll=poll,
        top_k=int(doc.get("top_k") or 10), pace_seconds=float(doc.get("pace_seconds") or 0.0),
        timeout_seconds=timeout or (1800.0 if poll else 120.0),
        concurrency=int(doc.get("concurrency") or (4 if poll else 1)),
    )


def load_profiles(arms: list[str] | None = None, *, directory: Path | None = None) -> dict[str, Profile]:
    """Every default arm, or the named arm ids (``exa.search``) or vendors (``exa``)."""
    folder = directory or module_root() / PROFILE_DIR
    profiles: dict[str, Profile] = {}
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        profile = parse_profile(_read_json(path), str(path))
        if path.name != f"{profile.arm_id}.json":
            raise InsightError(f"{path}: file name must be {profile.arm_id}.json")
        profiles[profile.arm_id] = profile
    if not arms:
        return {k: p for k, p in profiles.items() if p.default_arm}
    chosen: dict[str, Profile] = {}
    for name in arms:
        exact = {k: p for k, p in profiles.items() if k == name}
        vendor = {k: p for k, p in profiles.items() if p.arm == name and p.default_arm}
        if not (exact or vendor):
            raise InsightError(f"no profile for arm {name!r}; known: {', '.join(sorted(profiles))}")
        chosen.update(exact or vendor)
    return dict(sorted(chosen.items()))


def placeholders(
    question: Mapping[str, Any], profile: Profile, shared: Shared, run_date: date, *,
    writer: Mapping[str, Any] | None = None, extractor_model: str | None = None,
) -> dict[str, Any]:
    text = str(question["text"])
    since = run_date - timedelta(days=int(question.get("freshness_days") or 0))
    values: dict[str, Any] = {
        "QUERY": text,
        "RESEARCH_BRIEF": shared.research_brief.replace("{{QUERY}}", text),
        "EXTRACTOR_BRIEF": shared.extractor_brief.replace("{{QUERY}}", text),
        "EXTRACTOR_MODEL": extractor_model or "unpinned",
        "SESSION_ID": f"sl-{profile.arm_id}-{question['id']}-{run_date.isoformat()}",
        "AFTER_DATE": since.isoformat(), "AFTER_DATE_MDY": since.strftime("%m/%d/%Y"),
        "RUN_DATE": run_date.isoformat(), "RUN_DATE_MDY": run_date.strftime("%m/%d/%Y"),
    }
    if writer:
        values.update(OBJECTIVE=writer["objective"], KEYWORD_QUERIES=list(writer["keyword_queries"]))
    return values


def render(value: Any, values: Mapping[str, Any]) -> Any:
    """Fill ``{{NAME}}`` slots; a whole-string list slot becomes the list, ``{{NAME...}}`` spreads."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and isinstance(values.get(whole.group(1)), list):
            return list(values[whole.group(1)])
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), value)
    if isinstance(value, Mapping):
        return {k: render(v, values) for k, v in value.items()}
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            spread = _PLACEHOLDER.fullmatch(item) if isinstance(item, str) else None
            if spread and spread.group(2) and isinstance(values.get(spread.group(1)), list):
                out.extend(values[spread.group(1)])
            else:
                out.append(render(item, values))
        return out
    return value


def _path(path: str) -> list[str | int]:
    return [int(p[1:-1]) if p.startswith("[") else p for p in re.findall(r"\[\d+\]|[^.\[\]]+", path)]


def _set(doc: Any, path: list[str | int], value: Any) -> None:
    for part in path[:-1]:
        doc = doc[part] if isinstance(part, int) else doc.setdefault(part, {})
    last = path[-1]
    if isinstance(value, Mapping) and isinstance(doc[last] if isinstance(last, int) else doc.get(last), Mapping):
        doc[last] = {**doc[last], **value}
    else:
        doc[last] = value


def _get(doc: Any, path: list[str | int]) -> Any:
    for part in path:
        doc = doc[part] if isinstance(part, int) else doc.get(part)
    return doc


def apply_overlay(params: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Dotted-path keys set or merge; ``<path>_suffix`` appends text to the string at path."""
    out = copy.deepcopy(dict(params))
    for key, value in overlay.items():
        if key.endswith("_suffix"):
            path = _path(key[: -len("_suffix")])
            _set(out, path, f"{_get(out, path)}\n\n{value}")
        else:
            _set(out, _path(key), copy.deepcopy(value))
    return out


def request_body(profile: Profile, question: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The exact body this profile sends for one question."""
    params = dict(profile.params)
    overlay = profile.raw.get("freshness_overlay")
    if question.get("freshness_days") and isinstance(overlay, Mapping):
        params = apply_overlay(params, overlay)
    body = render(params, values)
    left = slots(body)
    if left:
        raise InsightError(f"{profile.arm_id}: unresolved placeholder(s) {sorted(left)}")
    return body

