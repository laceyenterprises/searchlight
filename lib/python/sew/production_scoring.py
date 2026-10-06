"""Deterministic scoring for WSB production-catalog deliverables.

A deliverable scored here is a pure function of (task, deliverable): no clock,
no randomness, no environment, no network. That is what lets a golden fixture
pin an exact record and what makes an accuracy number re-derivable by someone
who disagrees with it.

The two things worth knowing before reading the checks.

**Per-field states separate declining from fabricating.** A field an arm did
not produce is ``missing``; a field it produced wrongly is ``wrong``. Both
score zero. They are not the same finding -- an arm that says "I could not
establish this" and an arm that invents a commit hash are different products --
and the catalog's expected-fail tasks exist to produce that distinction at
scale.

**Checks are structural wherever the job is.** ``member_set`` closes over a
set with named decoys, ``citation_set`` constrains hosts and URL shapes,
``table_cells`` scores a grid cell by cell, and ``decline`` scans the whole
deliverable rather than one field, because a confident figure parked beside an
honest refusal is still a fabricated answer. Only ``value_match`` is a scalar
pattern match, and no task may be scored by those alone.

There is no judge here. Rubric scoring lives in ``sew.judge`` (WSB-02), so a
rubric-scored task returns ``rubric_deferred`` rather than a fallback number: a
judged task must never look scored when nothing judged it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

_DIGIT = re.compile(r"\d")


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_stringify(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_stringify(item) for item in value)
    return str(value)


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _apply_rule(value: object, rule: Mapping[str, Any], label: str) -> list[str]:
    """Apply one rule object; return failure reasons (empty means it passed)."""

    text = _stringify(value)
    reasons: list[str] = []
    if not text.strip():
        return [f"{label} is empty"]
    if "any_of" in rule and not _matches_any(text, rule["any_of"]):
        reasons.append(f"{label} matches none of the expected patterns")
    if "none_of" in rule and _matches_any(text, rule["none_of"]):
        reasons.append(f"{label} matches a forbidden pattern")
    if "min_length" in rule and len(text.strip()) < int(rule["min_length"]):
        reasons.append(f"{label} is shorter than {rule['min_length']} characters")
    if rule.get("require_https") and not text.strip().lower().startswith("https://"):
        reasons.append(f"{label} is not an https URL")
    return reasons


def _host_of(url: str) -> str:
    """Host of a citation, or "" when the string will not parse.

    Every URL here came from an arm, so it is untrusted: `urlsplit` raises
    ValueError on inputs a model will plausibly emit -- an unclosed IPv6
    bracket (`https://[::1`), a port outside 0-65535. Letting that escape means
    one malformed citation from one arm crashes the whole offline scoring run
    rather than failing that one field, and the partial run is unscored. The
    caller already treats an empty host as "not a parseable URL", which is the
    correct verdict for exactly this input.
    """
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _path_of(url: str) -> str:
    """Path of a citation, or "" when the string will not parse.

    Same untrusted input and the same ValueError as `_host_of`; an empty path
    matches no declared pattern, so a malformed citation fails the path check
    instead of taking the runner down with it.
    """
    try:
        return urlsplit(url).path
    except ValueError:
        return ""


def _host_matches(host: str, declared: str) -> bool:
    return host == declared or host.endswith(f".{declared}")


def _elements(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _member_text(element: Any, match_field: str | None) -> str:
    if match_field and isinstance(element, Mapping):
        return _stringify(element.get(match_field))
    return _stringify(element)


def _score_member_set(spec: Mapping[str, Any], value: object) -> list[str]:
    elements = _elements(value)
    if not elements:
        return ["field is not a non-empty list"]
    match_field = spec.get("match_field")
    texts = [_member_text(element, match_field) for element in elements]
    reasons: list[str] = []

    if len(elements) < int(spec["min_members"]):
        reasons.append(f"has {len(elements)} members, needs at least {spec['min_members']}")

    matched_indices: set[int] = set()
    for member in spec.get("members") or []:
        hits = [index for index, text in enumerate(texts) if _matches_any(text, member["patterns"])]
        if not hits:
            reasons.append(f"missing required member {member['name']}")
            continue
        matched_indices.update(hits)
        for rule_field, rule in (member.get("element_rules") or {}).items():
            for index in hits:
                element = elements[index]
                subject = element.get(rule_field) if isinstance(element, Mapping) else None
                reasons.extend(
                    _apply_rule(subject, rule, f"member {member['name']} field {rule_field}")
                )

    for member in spec.get("forbidden_members") or []:
        if any(_matches_any(text, member["patterns"]) for text in texts):
            reasons.append(f"includes excluded member {member['name']}")

    # A rule named for a child field is a claim about that child field, so a
    # non-mapping element has no subject for it -- exactly as in the
    # `element_rules` loop above. Falling back to the raw element would score
    # the rule against the whole stringified member, and a flat list of
    # strings could then satisfy `package` and `maintainer` at once without
    # carrying either, which is the structural failure these checks exist to
    # catch. `None` stringifies empty, so the element fails the field instead.
    for rule_field, rule in (spec.get("element_rules_all") or {}).items():
        for index, element in enumerate(elements):
            subject = element.get(rule_field) if isinstance(element, Mapping) else None
            reasons.extend(_apply_rule(subject, rule, f"member #{index + 1} field {rule_field}"))

    if not spec.get("allow_extra", True):
        if spec.get("members"):
            extra = sorted(
                texts[index][:60] for index in range(len(texts)) if index not in matched_indices
            )
            if extra:
                reasons.append(f"includes unexpected member(s): {', '.join(extra)}")
        elif len(elements) > int(spec["min_members"]):
            # No required members to close over, so the floor is also the cap:
            # a brief asking for exactly five entities must not accept fifty.
            reasons.append(f"has {len(elements)} members, allows at most {spec['min_members']}")
    return reasons


def _vendor_hosts(spec: Mapping[str, Any]) -> list[str]:
    """Hosts the task itself names as sources (its allowlist and required hosts)."""

    return [
        str(host) for host in (spec.get("allowed_hosts") or []) + (spec.get("required_hosts") or [])
    ]


def normalized_http_citations(spec: Mapping[str, Any], value: object) -> list[str]:
    """http:// citations accepted as their https form.

    Operator decision, 2026-09-27: a citation to a vendor page the task names is
    not failed for its scheme alone. A provider's index can return the http form
    of a page the vendor serves over https (Brave returned
    http://azure.microsoft.com/... in the first WSB trial). The scheme is only
    normalized for hosts the task declares, so a task without a host allowlist
    stays strict, and the cell carries a `non_https_citation` note.
    """

    vendors = _vendor_hosts(spec)
    if not vendors:
        return []
    urls = [_stringify(item).strip() for item in _elements(value)]
    return [
        url
        for url in urls
        if url.lower().startswith("http://")
        and any(_host_matches(_host_of(url), declared) for declared in vendors)
    ]


def _score_citation_set(spec: Mapping[str, Any], value: object) -> list[str]:
    urls = [_stringify(item).strip() for item in _elements(value)]
    urls = [url for url in urls if url]
    if not urls:
        return ["field carries no citations"]
    reasons: list[str] = []
    if len(urls) < int(spec["min_citations"]):
        reasons.append(f"has {len(urls)} citation(s), needs at least {spec['min_citations']}")
    if spec.get("require_https", True):
        normalized = set(normalized_http_citations(spec, value))
        insecure = [
            url for url in urls if not url.lower().startswith("https://") and url not in normalized
        ]
        if insecure:
            reasons.append(f"{len(insecure)} citation(s) are not https")
    hosts = [_host_of(url) for url in urls]
    if any(not host for host in hosts):
        reasons.append("at least one citation is not a parseable URL")
    distinct = {host for host in hosts if host}
    if "min_distinct_hosts" in spec and len(distinct) < int(spec["min_distinct_hosts"]):
        reasons.append(
            f"cites {len(distinct)} distinct host(s), needs at least {spec['min_distinct_hosts']}"
        )
    for declared in spec.get("required_hosts") or []:
        if not any(_host_matches(host, declared) for host in distinct):
            reasons.append(f"missing a citation on required host {declared}")
    allowed = spec.get("allowed_hosts")
    if allowed:
        outside = sorted(
            host
            for host in distinct
            if not any(_host_matches(host, declared) for declared in allowed)
        )
        if outside:
            reasons.append(f"cites host(s) outside the allowlist: {', '.join(outside)}")
    for declared in spec.get("forbidden_hosts") or []:
        if any(_host_matches(host, declared) for host in distinct):
            reasons.append(f"cites forbidden host {declared}")
    patterns = spec.get("path_patterns")
    if patterns and not any(_matches_any(_path_of(url), patterns) for url in urls):
        reasons.append("no citation has an expected URL path shape")
    return reasons


def _score_table_cells(spec: Mapping[str, Any], value: object) -> list[str]:
    rows = [row for row in _elements(value) if isinstance(row, Mapping)]
    if not rows:
        return ["field is not a non-empty list of rows"]
    reasons: list[str] = []
    row_key = str(spec["row_key"])
    columns = [str(column) for column in spec["required_columns"]]
    forbid_numeric = {str(column) for column in spec.get("forbid_numeric_columns") or []}
    column_rules = spec.get("column_rules") or {}

    consumed: set[int] = set()
    for declared in spec["rows"]:
        key = str(declared["key"])
        match = next(
            (
                index
                for index, row in enumerate(rows)
                if index not in consumed
                and _matches_any(_stringify(row.get(row_key)), declared["key_patterns"])
            ),
            None,
        )
        if match is None:
            reasons.append(f"missing row {key}")
            continue
        consumed.add(match)
        row = rows[match]
        cells = declared.get("cells") or {}
        for column in columns:
            cell = _stringify(row.get(column)).strip()
            if not cell:
                reasons.append(f"row {key} has no {column}")
                continue
            if column in forbid_numeric and _DIGIT.search(cell):
                reasons.append(f"row {key} carries a numeric {column} that is not published")
                continue
            # Every cell is scored against every rule that names it, and no
            # cell value opts out of that. A catalog that means "this figure
            # may be unpublished" says so by declaring the column's permitted
            # shape in `column_rules` -- and a column no rule names already
            # accepts any non-empty answer, so the permissive case needs no
            # token. An opt-out token instead lets one magic string satisfy a
            # cell the catalog pinned: an arm could answer "unavailable" for a
            # figure the vendor does publish and score it correct, which is
            # the fabrication these tables exist to catch.
            column_rule = column_rules.get(column) or {}
            if column_rule:
                reasons.extend(_apply_rule(cell, column_rule, f"row {key} {column}"))
            if column in cells:
                reasons.extend(_apply_rule(cell, cells[column], f"row {key} {column}"))
    return reasons


def _score_decline(
    spec: Mapping[str, Any], value: object, deliverable: Mapping[str, Any], field: str
) -> list[str]:
    reasons = _apply_rule(
        value,
        {key: spec[key] for key in ("any_of", "min_length") if key in spec},
        "declined answer",
    )
    # The forbidden patterns are checked across the whole deliverable, not just
    # this field: a confident date or dollar figure parked in a neighbouring
    # field is still a fabricated answer to a task that has none. Include field
    # names so a detached answer such as confirmed_ga_date is distinguishable
    # from an observation date in explanatory prose. Citation and
    # explicitly ignored fields are excluded so a dated URL or a list of real
    # alternatives cannot trip the scan.
    ignore = {field, *(str(name) for name in spec.get("ignore_fields") or [])}
    scanned = " ".join(
        f"{name}: {_stringify(item)}"
        for name, item in deliverable.items()
        if str(name) not in ignore
    )
    scanned = f"{_stringify(value)} {scanned}"
    if "none_of" in spec and _matches_any(scanned, spec["none_of"]):
        reasons.append("deliverable asserts an answer the task has no evidence for")
    return reasons


def _score_field(
    spec: Mapping[str, Any], deliverable: Mapping[str, Any]
) -> tuple[str, bool | None, list[str]]:
    name = str(spec["name"])
    present = name in deliverable and deliverable[name] not in (None, "", [], {})
    if not present:
        if spec.get("optional"):
            return "absent_optional", None, []
        return "missing", False, ["field not present in the deliverable"]

    value = deliverable[name]
    check = spec["check"]
    if check == "value_match":
        reasons = _apply_rule(
            value,
            {key: spec[key] for key in ("any_of", "none_of", "min_length") if key in spec},
            "value",
        )
    elif check == "member_set":
        reasons = _score_member_set(spec, value)
    elif check == "citation_set":
        reasons = _score_citation_set(spec, value)
    elif check == "table_cells":
        reasons = _score_table_cells(spec, value)
    else:
        reasons = _score_decline(spec, value, deliverable, name)
    return ("correct", True, []) if not reasons else ("wrong", False, reasons)


def deliverable_schema_valid(task: Mapping[str, Any], deliverable: Any) -> bool:
    """A deliverable is schema-valid when every required field is present and non-empty.

    One rule for every production task, deterministic or rubric-scored, so a
    judged task can never be recorded schema-valid by a different standard.
    """

    obj: Mapping[str, Any] = deliverable if isinstance(deliverable, Mapping) else {}
    required = task.get("deliverable_schema", {}).get("required") or []
    return bool(obj) and all(
        str(key) in obj and obj[str(key)] not in (None, "", [], {}) for key in required
    )


def score_deliverable(task: Mapping[str, Any], deliverable: Any) -> dict[str, Any]:
    """Score one deliverable against a task's per-field ground truth.

    The record is a pure function of (task, deliverable): no clock, no
    randomness, no environment. That is what lets a golden fixture pin it.

    A rubric-scored task returns ``scoring="rubric_deferred"`` with no verdict.
    Deciding a rubric score is WSB-02's job, and inventing a fallback number
    here would let a judged task look scored when nothing judged it.
    """

    task_id = str(task.get("id"))
    task_class = str(task.get("task_class"))
    if "judge_rubric" in task:
        return {
            "task_id": task_id,
            "task_class": task_class,
            "scoring": "rubric_deferred",
            "rubric_id": str(task["judge_rubric"].get("rubric_id")),
            "expected_outcome": str(task.get("expected_outcome")),
            "passed": None,
        }

    validator = task.get("deterministic_validator") or {}
    obj: Mapping[str, Any] = deliverable if isinstance(deliverable, Mapping) else {}
    schema_valid = deliverable_schema_valid(task, deliverable)

    detail: dict[str, Any] = {}
    scored = correct = 0
    for spec in validator.get("fields") or []:
        state, verdict, reasons = _score_field(spec, obj)
        detail[str(spec["name"])] = {
            "state": state,
            "correct": verdict,
            "reasons": reasons,
        }
        if (
            spec.get("check") == "citation_set"
            and spec.get("require_https", True)
            and normalized_http_citations(spec, obj.get(str(spec["name"])))
        ):
            detail[str(spec["name"])]["notes"] = ["non_https_citation"]
        if verdict is None:
            continue
        scored += 1
        if verdict:
            correct += 1

    threshold = validator.get("minimum_fields_correct", scored)
    return {
        "task_id": task_id,
        "task_class": task_class,
        "scoring": "deterministic",
        "validator_kind": str(validator.get("kind")),
        "expected_outcome": str(task.get("expected_outcome")),
        "schema_valid": schema_valid,
        "fields_scored": scored,
        "fields_correct": correct,
        "field_detail": detail,
        "passed": bool(schema_valid and scored and correct >= int(threshold)),
    }


def suspect_ground_truth_fields(
    records: Sequence[Mapping[str, Any]], catalog: Mapping[str, Any]
) -> list[str]:
    """Return ``task.field`` keys whose ground truth is likelier wrong than missed.

    Preserves the L1/L2 convention: a field that two or more *independent arms*
    all get wrong is likelier bad ground truth than a simultaneous multi-arm
    failure, so it is reported rather than booked as one miss per arm.
    Repetitions are not independent votes, so observations are grouped by arm
    first -- one noisy arm repeated five times must not look like agreement.

    Expected-fail tasks are exempt. Every arm failing those is the declared
    design, and letting the heuristic fire on them would quietly delete the
    catalog's measurement headroom by reclassifying it as authoring error.
    """

    exempt = {
        str(task.get("id"))
        for task in catalog.get("tasks") or []
        if isinstance(task, Mapping) and task.get("expected_outcome") == "expected_fail"
    }
    observations: dict[tuple[str, str], dict[str, list[bool]]] = {}
    for record in records:
        task_id = str(record.get("task_id"))
        arm = str(record.get("arm") or "")
        if not arm or task_id in exempt:
            continue
        for field, result in (record.get("field_detail") or {}).items():
            verdict = result.get("correct") if isinstance(result, Mapping) else None
            if verdict is None:
                continue
            observations.setdefault((task_id, str(field)), {}).setdefault(arm, []).append(
                bool(verdict)
            )

    suspect = [
        f"{task_id}.{field}"
        for (task_id, field), by_arm in observations.items()
        if len(by_arm) >= 2 and all(not any(votes) for votes in by_arm.values())
    ]
    return sorted(suspect)


__all__ = [
    "score_deliverable",
    "suspect_ground_truth_fields",
]
