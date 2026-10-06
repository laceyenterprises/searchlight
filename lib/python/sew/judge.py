"""WSB-02 blinded judge harness for rubric-scored production deliverables.

Half the WSB task classes produce deliverables with no single right answer, so
a judge model scores them against the rubric the catalog declares. A judge that
can tell which arm produced a deliverable is no longer measuring the work, so
the payload is blinded before any judge sees it.

Three properties are enforced rather than documented.

**Blinding is arm-independent.** ``build_judge_payload`` takes no arm argument.
It redacts one fixed vocabulary of provider, harness, model and tool names from
every payload, replaces every URL and bare hostname with an opaque ``[source-N]``
reference, and projects the deliverable onto the fields the brief's schema
declares, so tool traces and provider-shaped extras never reach the judge.
Because nothing arm-specific goes in, identical deliverables produce
byte-identical payloads under any arm label, and the identical-score property
follows from the construction instead of from a judge happening to be
consistent. The ``payload_sha256`` on every record lets an auditor check it.

``find_identity_leaks`` is the post-condition. Given the arm that produced the
deliverable, it searches the finished payload for that arm's identifiers and for
anything still shaped like a URL or hostname. A leak fails the cell as
``blinding_failed`` before any judge is called; it never scores unblinded.

The fixed vocabulary has a cost that is paid uniformly. ``parallel`` is an
English word as well as a provider name, and it is redacted everywhere, for
every arm. Redacting it for one arm only would itself leak which arm that was.

**Scoring is per dimension.** A judge must return a score and a non-empty
reason for every rubric dimension, and nothing else. A holistic score alone is
an invalid result, not a fallback. A deliverable passes only when every
dimension reaches the minimum, so one failed dimension cannot be averaged away
and the report can say which dimension an arm failed.

**Disagreement is reported, not averaged.** The first judge decides the verdict.
Every further judge is a measurement of that verdict: each record carries a
per-dimension score spread and verdict agreement, and a record whose judges
disagree on pass/fail is marked ``verdict_disputed``. ``summarize_agreement``
pools the records of a run into exact agreement, within-one agreement, verdict
agreement and quadratic-weighted Cohen's kappa between the first two judges.
A record judged once says so (``not_measured``) rather than implying agreement.

Judges are plain callables. Unit tests pass stubs; a live transport is WSB-07's
concern, and nothing here opens a network connection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .providers import PROVIDER_CREDENTIALS
from .schema import SchemaError

PAYLOAD_VERSION = "wsb-judge-payload-v1"
RECORD_VERSION = "wsb-judge-record-v1"
REDACTED = "[redacted]"

JudgeTransport = Callable[[Mapping[str, Any]], Mapping[str, Any]]

# One vocabulary for every arm, matched case-insensitively between
# non-alphanumeric boundaries. Underscore is a boundary on purpose: tool names
# such as `mcp__exa__search` are underscore-joined, and a `\b`-style boundary
# would let the provider name inside them through.
_IDENTITY_CORES: tuple[str, ...] = (
    # Search providers under test, plus common alternatives an arm may mention.
    *(re.escape(provider_id) for provider_id in sorted(PROVIDER_CREDENTIALS)),
    "exa",
    "parallel",
    "firecrawl",
    "tavily",
    "perplexity",
    "serp[_-]?api",
    "bing",
    # Harnesses and model families.
    "claude",
    "anthropic",
    "codex",
    "openai",
    "chatgpt",
    "gpt",
    "gemini",
    "sonnet",
    "opus",
    "haiku",
    "fable",
    # Tool surfaces. A tool trace names the arm as surely as a label does.
    "mcp",
    "web[_-]?search",
    "web[_-]?fetch",
    "no[_-]search",
)
_IDENTITY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(_IDENTITY_CORES) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# The unit of redaction is the whole joined run a core appears in, not the core.
# Redacting `exa` inside `mcp__exa__web_search_exa` core by core would leave
# `[redacted]__[redacted]__[redacted]_[redacted]`, whose shape still tells an
# MCP arm from a native one; `claude-code+native` would keep its `+native`.
_JOINED_RUN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.+-]*[A-Za-z0-9])?")

_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"'`)\]}]+")
# Bare hostnames are matched against a closed TLD list rather than any
# `word.word`: `setup.py`, `config.yaml` and `README.md` are not citations, and
# `.md`/`.py` are real TLDs, so an open pattern would shred ordinary prose.
_BARE_HOST_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.-])(?:www\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"(?:com|org|net|io|ai|dev|app|co|gov|edu|info|sh|so|xyz|cloud|tech|us|uk)"
    r"(?![A-Za-z0-9-])(?:/[^\s<>\"'`)\]}]*)?",
    re.IGNORECASE,
)

_COMMIT_PINNED = re.compile(r"/(?:-/)?(?:commit|commits|blob|tree)/[0-9a-f]{7,40}(?:/|$)", re.I)
_ISSUE_OR_PR = re.compile(r"/(?:issues|pull|pulls|merge_requests|discussions)/\d+", re.I)
_RELEASE_OR_TAG = re.compile(r"/(?:releases|tags?)(?:/|$)", re.I)

# Keys dropped from a deliverable whose schema declares no properties to project
# onto. A schema that does declare properties is stricter: only declared keys
# survive.
_TRACE_KEYS = frozenset(
    {
        "arm",
        "arm_id",
        "arm_label",
        "harness",
        "harness_id",
        "provider",
        "provider_id",
        "model",
        "model_id",
        "model_profile",
        "tool_calls",
        "tool_trace",
        "tool_traces",
        "tools",
        "transcript",
        "trace",
        "run_id",
        "cell_id",
        "usage",
        "telemetry",
        "_meta",
    }
)

JUDGE_INSTRUCTIONS = (
    "You are scoring one deliverable against a rubric. Score every rubric dimension "
    "separately on the given integer scale and give a specific reason for each score. "
    "Do not give an overall score. Citations appear as opaque [source-N] references with "
    "their URL shape only; judge whether claims are cited, not where they are cited from. "
    "Redacted spans appear as [redacted]; do not speculate about what they contained."
)
RESPONSE_CONTRACT = {
    "dimensions": {"<dimension>": {"score": "<integer within score_scale>", "reason": "<text>"}}
}


class BlindingError(SchemaError):
    """The judge payload still carries an arm-identifying token."""


@dataclass(frozen=True)
class ArmIdentity:
    """Everything that names the arm a deliverable came from.

    Only the leak check reads this. It never reaches the payload builder, which
    is why blinding cannot depend on the arm.
    """

    arm_id: str
    harness_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    tool_names: tuple[str, ...] = ()

    def identifiers(self) -> list[str]:
        # Reference and built-in arm labels name a condition, not a vendor, and
        # are ordinary words ("native" integration, price "floor"): matching them
        # failed real briefs as blinding_failed. The compound arm id, harness,
        # model, provider names and tool names still identify the arm.
        provider = None if self.provider_id in GENERIC_ARM_LABELS else self.provider_id
        values = [self.arm_id, self.harness_id, provider, self.model_id, *self.tool_names]
        return sorted({value.strip() for value in values if value and value.strip()})


# pipeline-label-coverage: non-pr — search evaluation arm names, not PR controls.
GENERIC_ARM_LABELS = frozenset({"native", "floor", "ceiling", "no-search"})


@dataclass(frozen=True)
class Judge:
    """One judge: a stable id for the record and a transport that scores a payload."""

    judge_id: str
    transport: JudgeTransport


@dataclass
class _Blinder:
    citations: dict[str, str] = field(default_factory=dict)
    redactions: int = 0
    dropped_fields: list[str] = field(default_factory=list)

    def cite(self, match: re.Match[str]) -> str:
        target = match.group(0).rstrip(".,;:")
        tail = match.group(0)[len(target) :]
        ref = self.citations.setdefault(target, f"source-{len(self.citations) + 1}")
        return f"[{ref}]{tail}"

    def redact(self, match: re.Match[str]) -> str:
        if not _IDENTITY_PATTERN.search(match.group(0)):
            return match.group(0)
        self.redactions += 1
        return REDACTED

    def text(self, value: str) -> str:
        if "://" in value:
            value = _URL_PATTERN.sub(self.cite, value)
        value = _BARE_HOST_PATTERN.sub(self.cite, value)
        return _JOINED_RUN.sub(self.redact, value)

    def value(self, value: Any, schema: Any, path: str, *, drop_keys: bool = True) -> Any:
        schema = schema if isinstance(schema, Mapping) else {}
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            properties = schema.get("properties")
            out: dict[str, Any] = {}
            # Sorted traversal numbers citations by content, not by the key
            # order an arm happened to serialize in.
            for key in sorted(value, key=str):
                child_path = f"{path}.{key}" if path else str(key)
                if not drop_keys:
                    child_schema = {}
                elif isinstance(properties, Mapping) and properties:
                    if key not in properties:
                        self.dropped_fields.append(child_path)
                        continue
                    child_schema = properties[key]
                elif str(key).casefold() in _TRACE_KEYS:
                    self.dropped_fields.append(child_path)
                    continue
                else:
                    child_schema = {}
                out[self.text(str(key))] = self.value(
                    value[key], child_schema, child_path, drop_keys=drop_keys
                )
            return out
        if isinstance(value, (list, tuple)):
            items = schema.get("items")
            return [
                self.value(item, items, f"{path}[{i}]", drop_keys=drop_keys)
                for i, item in enumerate(value)
            ]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.text(str(value))


def _citation_shape(target: str) -> dict[str, Any]:
    try:
        parts = urlsplit(target if "://" in target else f"//{target}")
        path, scheme = parts.path, parts.scheme or None
    except ValueError:
        return {"scheme": None, "shape": "unparseable"}
    if _COMMIT_PINNED.search(path):
        shape = "commit_pinned"
    elif _ISSUE_OR_PR.search(path):
        shape = "issue_or_pr"
    elif _RELEASE_OR_TAG.search(path):
        shape = "release_or_tag"
    elif path in ("", "/"):
        shape = "site_root"
    else:
        shape = "document"
    return {"scheme": scheme, "shape": shape}


def resolve_rubric(task: Mapping[str, Any], rubric_registry: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a task's ``judge_rubric`` binding against the rubric registry."""

    binding = task.get("judge_rubric")
    if not isinstance(binding, Mapping):
        raise SchemaError(f"task {task.get('id')} declares no judge_rubric")
    rubric_id = binding.get("rubric_id")
    for rubric in rubric_registry.get("rubrics") or []:
        if isinstance(rubric, Mapping) and rubric.get("rubric_id") == rubric_id:
            resolved = dict(rubric)
            resolved["minimum_score"] = binding.get("minimum_score", rubric["minimum_score"])
            return resolved
    raise SchemaError(f"task {task.get('id')} names unknown rubric {rubric_id!r}")


def build_judge_payload(
    task: Mapping[str, Any], rubric: Mapping[str, Any], deliverable: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(payload, blinding_report)`` for one deliverable.

    Deliberately takes no arm. The report lists what was stripped and is kept
    with the judge record for the evidence bundle; it is never sent to a judge.
    """

    schema = task.get("deliverable_schema") or {}
    blinder = _Blinder()
    # Deliverable first, so citation numbering depends only on the deliverable.
    blinded_deliverable = blinder.value(deliverable, schema, "")
    task_view = {
        "task_class": str(task.get("task_class")),
        "prompt": blinder.text(str(task.get("prompt", ""))),
        # The schema is catalog-authored, so it is redacted but never pruned.
        "deliverable_schema": blinder.value(schema, {}, "", drop_keys=False),
    }
    payload = {
        "payload_version": PAYLOAD_VERSION,
        "instructions": JUDGE_INSTRUCTIONS,
        "task": task_view,
        "rubric": {
            "rubric_id": str(rubric["rubric_id"]),
            "description": blinder.text(str(rubric.get("description", ""))),
            "dimensions": [str(dimension) for dimension in rubric["dimensions"]],
            "score_scale": dict(rubric["score_scale"]),
            "minimum_score": rubric["minimum_score"],
        },
        "deliverable": blinded_deliverable,
        "citations": [
            {"ref": ref, **_citation_shape(target)} for target, ref in blinder.citations.items()
        ],
        "response_contract": RESPONSE_CONTRACT,
    }
    report = {
        "dropped_fields": list(blinder.dropped_fields),
        "redacted_spans": blinder.redactions,
        "citations_normalized": len(blinder.citations),
    }
    return payload, report


def _serialize(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_serialize(payload).encode("utf-8")).hexdigest()


def _token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", re.IGNORECASE)


def find_identity_leaks(payload: Mapping[str, Any], arm: ArmIdentity | None = None) -> list[str]:
    """Return every arm-identifying token still present in a judge payload.

    Checks the arm's own identifiers, the fixed identity vocabulary, and any
    surviving URL or hostname. An empty list is the only passing result.
    """

    # Scan the decoded strings the judge will read, not their JSON encoding: in
    # JSON a newline becomes a literal "\n", which glued a changelog's bare
    # "https://" onto the next line ("https://\n#947") and failed the cell.
    text = "\n".join(_strings(payload))
    leaks: list[str] = []
    for token in arm.identifiers() if arm else []:
        if _token_pattern(token).search(text):
            leaks.append(f"arm_identifier:{token}")
    for pattern, kind in (
        (_IDENTITY_PATTERN, "identity_term"),
        (_URL_PATTERN, "url"),
        (_BARE_HOST_PATTERN, "hostname"),
    ):
        if pattern is _URL_PATTERN and "://" not in text:
            continue
        for match in pattern.finditer(text):
            leaks.append(f"{kind}:{match.group(0)}")
    return sorted(set(leaks))


def _strings(value: Any) -> list[str]:
    """Every key and string value in a payload, depth first."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [
            text for key, item in value.items() for text in (*_strings(str(key)), *_strings(item))
        ]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _strings(item)]
    return []


def blind_for_judge(
    task: Mapping[str, Any],
    rubric: Mapping[str, Any],
    deliverable: Any,
    arm: ArmIdentity | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the payload and refuse it if any arm-identifying token survived."""

    payload, report = build_judge_payload(task, rubric, deliverable)
    leaks = find_identity_leaks(payload, arm)
    if leaks:
        raise BlindingError(f"judge payload leaks arm identity: {', '.join(leaks)}")
    return payload, report


def _score_one(
    judge: Judge, payload: Mapping[str, Any], rubric: Mapping[str, Any]
) -> dict[str, Any]:
    record: dict[str, Any] = {"judge_id": judge.judge_id}
    try:
        # Each judge gets its own copy, so one transport cannot alter what the
        # next one is shown.
        response = judge.transport(copy.deepcopy(payload))
    except Exception as exc:  # noqa: BLE001 - a judge failure is a recorded outcome.
        return {**record, "status": "error", "errors": [f"transport:{type(exc).__name__}"]}

    dimensions = [str(dimension) for dimension in rubric["dimensions"]]
    scale = rubric["score_scale"]
    minimum = rubric["minimum_score"]
    raw = response.get("dimensions") if isinstance(response, Mapping) else None
    if not isinstance(raw, Mapping):
        return {**record, "status": "invalid", "errors": ["dimensions_missing"]}

    errors = [
        f"unknown_dimension:{name}" for name in sorted(map(str, raw)) if name not in dimensions
    ]
    scored: dict[str, dict[str, Any]] = {}
    for name in dimensions:
        entry = raw.get(name)
        if not isinstance(entry, Mapping):
            errors.append(f"dimension_missing:{name}")
            continue
        score, reason = entry.get("score"), entry.get("reason")
        if isinstance(score, bool) or not isinstance(score, int):
            errors.append(f"score_not_integer:{name}")
            continue
        if not scale["min"] <= score <= scale["max"]:
            errors.append(f"score_out_of_scale:{name}")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"reason_missing:{name}")
            continue
        scored[name] = {"score": score, "reason": reason.strip(), "passed": score >= minimum}
    if errors:
        return {**record, "status": "invalid", "errors": errors}

    if isinstance(response.get("model_profile"), str):
        record["model_profile"] = response["model_profile"]
    failed = [name for name in dimensions if not scored[name]["passed"]]
    return {
        **record,
        "status": "scored",
        "dimensions": scored,
        "failed_dimensions": failed,
        "passed": not failed,
    }


def _record_agreement(
    judge_records: Sequence[Mapping[str, Any]], dimensions: Sequence[str]
) -> dict[str, Any]:
    scored = [record for record in judge_records if record.get("status") == "scored"]
    if len(judge_records) < 2:
        return {"status": "not_measured", "reason": "single_judge", "judges": len(judge_records)}
    if len(scored) < 2:
        unscored = [r["judge_id"] for r in judge_records if r.get("status") != "scored"]
        return {
            "status": "not_measured",
            "reason": "judge_unscored",
            "judges": len(judge_records),
            "unscored_judges": unscored,
        }
    per_dimension: dict[str, dict[str, Any]] = {}
    for name in dimensions:
        scores = [record["dimensions"][name]["score"] for record in scored]
        verdicts = {record["dimensions"][name]["passed"] for record in scored}
        per_dimension[name] = {
            "scores": scores,
            "spread": max(scores) - min(scores),
            "verdict_agrees": len(verdicts) == 1,
        }
    count = len(dimensions)
    return {
        "status": "measured",
        "judges": len(scored),
        "dimensions": per_dimension,
        "exact_agreement": sum(d["spread"] == 0 for d in per_dimension.values()) / count,
        "within_one_agreement": sum(d["spread"] <= 1 for d in per_dimension.values()) / count,
        "dimension_verdict_agreement": sum(d["verdict_agrees"] for d in per_dimension.values())
        / count,
        "max_spread": max(d["spread"] for d in per_dimension.values()),
        "verdict_disputed": len({record["passed"] for record in scored}) > 1,
    }


def judge_deliverable(
    task: Mapping[str, Any],
    rubric: Mapping[str, Any],
    deliverable: Any,
    *,
    judges: Sequence[Judge],
    arm: ArmIdentity | None = None,
) -> dict[str, Any]:
    """Score one deliverable per rubric dimension with one or more blinded judges.

    ``judges[0]`` decides the verdict; every further judge measures agreement
    with it. ``arm`` is used only to check the payload for leaks and to label
    the record for the report -- no judge ever sees it.
    """

    if not judges:
        raise ValueError("judge_deliverable needs at least one judge")
    judge_ids = [judge.judge_id for judge in judges]
    if len(set(judge_ids)) != len(judge_ids):
        raise ValueError("judge ids must be distinct")

    dimensions = [str(dimension) for dimension in rubric["dimensions"]]
    record: dict[str, Any] = {
        "record_version": RECORD_VERSION,
        "task_id": str(task.get("id")),
        "task_class": str(task.get("task_class")),
        "scoring": "rubric",
        "rubric_id": str(rubric["rubric_id"]),
        "score_scale": dict(rubric["score_scale"]),
        "minimum_score": rubric["minimum_score"],
        "arm_id": arm.arm_id if arm else None,
    }

    payload, report = build_judge_payload(task, rubric, deliverable)
    record["payload_sha256"] = payload_digest(payload)
    record["blinding"] = report
    leaks = find_identity_leaks(payload, arm)
    if leaks:
        record["blinding"] = {**report, "leaks": leaks}
        return {
            **record,
            "status": "blinding_failed",
            "passed": False,
            "failed_dimensions": [],
            "dimensions": {},
            "judges": [],
            "agreement": {"status": "not_measured", "reason": "blinding_failed"},
        }

    judge_records = [_score_one(judge, payload, rubric) for judge in judges]
    primary = judge_records[0]
    if primary["status"] == "scored":
        status, passed = "scored", primary["passed"]
        verdict_dimensions = primary["dimensions"]
        failed = primary["failed_dimensions"]
    else:
        status = "judge_unavailable" if primary["status"] == "error" else "judge_invalid_result"
        passed, verdict_dimensions, failed = False, {}, []
    return {
        **record,
        "status": status,
        "passed": passed,
        "failed_dimensions": failed,
        "dimensions": verdict_dimensions,
        "judges": judge_records,
        "agreement": _record_agreement(judge_records, dimensions),
    }


def quadratic_weighted_kappa(
    first: Sequence[int], second: Sequence[int], *, minimum: int, maximum: int
) -> float | None:
    """Cohen's kappa with quadratic weights for two raters on an integer scale.

    Returns ``None`` when chance disagreement is zero (both raters used one
    identical value throughout), where kappa is undefined rather than 1.
    """

    if len(first) != len(second):
        raise ValueError("rater sequences must be the same length")
    if not first:
        return None
    k = maximum - minimum + 1
    if k < 2:
        return None
    n = len(first)
    observed = [[0.0] * k for _ in range(k)]
    for a, b in zip(first, second, strict=True):
        observed[a - minimum][b - minimum] += 1
    row = [sum(observed[i]) for i in range(k)]
    col = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weighted_observed = weighted_expected = 0.0
    for i in range(k):
        for j in range(k):
            weight = (i - j) ** 2 / (k - 1) ** 2
            weighted_observed += weight * observed[i][j]
            weighted_expected += weight * row[i] * col[j] / n
    if weighted_expected == 0:
        return None
    return 1 - weighted_observed / weighted_expected


def summarize_agreement(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Pool inter-judge agreement across a run's judge records.

    Compares the first two scored judges of each record dimension by dimension.
    Records with no measured agreement are counted, not silently dropped, so a
    run where the second judge mostly failed cannot report a flattering rate.
    """

    records = list(records)
    pairs: list[tuple[str, str, int, int, bool]] = []
    scales: set[tuple[int, int]] = set()
    disputed = unmeasured = 0
    for record in records:
        agreement = record.get("agreement") or {}
        if agreement.get("status") != "measured":
            unmeasured += 1
            continue
        disputed += bool(agreement.get("verdict_disputed"))
        scale = record["score_scale"]
        scales.add((scale["min"], scale["max"]))
        for name, entry in agreement["dimensions"].items():
            a, b = entry["scores"][:2]
            pairs.append((str(record["rubric_id"]), name, a, b, entry["verdict_agrees"]))

    summary: dict[str, Any] = {
        "records": len(records),
        "records_measured": len(records) - unmeasured,
        "records_unmeasured": unmeasured,
        "verdicts_disputed": disputed,
        "dimension_pairs": len(pairs),
    }
    if not pairs:
        return {**summary, "status": "not_measured"}

    def rates(items: Sequence[tuple[str, str, int, int, bool]]) -> dict[str, Any]:
        return {
            "n": len(items),
            "exact_agreement": sum(a == b for _, _, a, b, _ in items) / len(items),
            "within_one_agreement": sum(abs(a - b) <= 1 for _, _, a, b, _ in items) / len(items),
            "verdict_agreement": sum(agree for *_, agree in items) / len(items),
        }

    kappa = None
    if len(scales) == 1:
        (low, high) = next(iter(scales))
        kappa = quadratic_weighted_kappa(
            [a for _, _, a, _, _ in pairs],
            [b for _, _, _, b, _ in pairs],
            minimum=low,
            maximum=high,
        )
    by_dimension: dict[str, list[tuple[str, str, int, int, bool]]] = {}
    for item in pairs:
        by_dimension.setdefault(f"{item[0]}/{item[1]}", []).append(item)
    return {
        **summary,
        "status": "measured",
        **rates(pairs),
        "quadratic_weighted_kappa": kappa,
        "by_dimension": {key: rates(items) for key, items in sorted(by_dimension.items())},
    }


__all__ = [
    "ArmIdentity",
    "BlindingError",
    "JUDGE_INSTRUCTIONS",
    "Judge",
    "JudgeTransport",
    "PAYLOAD_VERSION",
    "RECORD_VERSION",
    "REDACTED",
    "blind_for_judge",
    "build_judge_payload",
    "find_identity_leaks",
    "judge_deliverable",
    "payload_digest",
    "quadratic_weighted_kappa",
    "resolve_rubric",
    "summarize_agreement",
]
