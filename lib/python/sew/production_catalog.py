"""WSB production task catalog: offline validation and publication readiness.

This module is the loader and the gate for the truth schema declared in
``catalogs/production/``. Scoring lives next door in ``production_scoring``;
its entry points are re-exported here so callers have one import site.

Two jobs:

* **validate** the catalog and its rubric registry with no network access, and
  refuse a catalog that has lost a property the suite depends on -- every task
  scored by exactly one instrument, every required deliverable field actually
  scored, budgets present and inside the suite ceilings, freshness windows that
  are internally consistent, at least one non-scalar check per task, and enough
  expected-fail tasks spread across enough classes to leave measurement
  headroom;
* **report** what would block publication -- truth that was asserted by the
  catalog author rather than re-fetched, and live-condition truth that has aged
  out of the window the task itself declared.

The validation is deliberately hostile to its own catalog. Most of these rules
exist because the failure they prevent is invisible on the page: a required
field nothing scores is a free pass, a required citation host outside its own
allowlist can never be satisfied, and a ``revalidate_by`` that has drifted away
from ``as_of + window_days`` makes a stale task look freshly windowed. Each
rule has a mutation test that breaks the property and asserts the refusal,
because a guard nobody has watched fire is not a guard.

There is no runner here (WSB-07). Rubric-scored tasks are judged by
``sew.judge`` (WSB-02), which resolves their rubric from this registry.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

# Intra-package reuse of the DSB gate's helpers, deliberately rather than by
# copy: both catalogs load YAML mappings, reject unknown keys strictly, and
# validate ISO dates the same way, and a second copy of that logic would let
# the two offline gates drift apart in exactly the places a reviewer cannot
# see. They stay private to `sew`.
from .catalog import (
    _load_mapping,
    _reject_unknown,
    _validate_iso_date,
    module_root,
)

# `_host_matches` is the scorer's own host predicate. The preflight imports it
# rather than restating subdomain matching, because the whole point of the
# allowlist check is to predict what the scorer will do with the same catalog.
from .production_scoring import (
    _host_matches,
    score_deliverable,
    suspect_ground_truth_fields,
)
from .schema import SchemaError

TASKS_PER_CLASS = 3
MIN_EXPECTED_FAIL_TASKS = 3
MIN_EXPECTED_FAIL_CLASSES = 3

# Each class declares the validator kinds it may use. The mapping exists so a
# task cannot be scored by an instrument built for a different shape of job --
# an `unanswerable` task validated as an `entity_set`, say, would pass by
# returning entities and the catalog would silently lose its honesty probe.
CLASS_VALIDATOR_KINDS: dict[str, frozenset[str]] = {
    "list_build": frozenset({"entity_set"}),
    "change_detection": frozenset({"change_set"}),
    "upstream_diagnosis": frozenset({"upstream_reference"}),
    "competitive_table": frozenset({"comparison_table"}),
    "multi_hop_entity": frozenset({"entity_chain"}),
    "unanswerable": frozenset({"supported_decline"}),
}
VALIDATOR_KINDS = frozenset().union(*CLASS_VALIDATOR_KINDS.values())

# Checks that cannot be satisfied by matching one token in one scalar field.
# Validation requires every deterministic task to use at least one of them, so
# "answer is a regex-matchable word" cannot re-enter the catalog the way it did
# in L2.
STRUCTURAL_CHECKS = frozenset({"member_set", "citation_set", "table_cells", "decline"})
CHECK_KINDS = STRUCTURAL_CHECKS | {"value_match"}

TRUTH_STATUSES = frozenset({"immutable_history", "current_state", "none"})
VERIFICATION_METHODS = frozenset({"author_assertion", "operator_review", "live_fetch"})
EXPECTED_OUTCOMES = frozenset({"competitive", "expected_fail"})
DRIFT_RISKS = frozenset({"low", "medium", "high"})

_CATALOG_KEYS = frozenset(
    {
        "schema_version",
        "catalog_id",
        "suite",
        "version",
        "description",
        "authoring_policy",
        "rubrics_ref",
        "budget_ceilings",
        "task_classes",
        "tasks",
    }
)
_RUBRIC_REGISTRY_KEYS = frozenset({"schema_version", "registry_id", "version", "rubrics"})
_RUBRIC_KEYS = frozenset(
    {
        "rubric_id",
        "description",
        "score_scale",
        "minimum_score",
        "dimensions",
        "blinding_note",
    }
)
_TASK_KEYS = frozenset(
    {
        "id",
        "task_class",
        "prompt",
        "freshness",
        "budgets",
        "deliverable_schema",
        "evidence_field",
        "expected_outcome",
        "expected_fail_reason",
        "truth_provenance",
        "deterministic_validator",
        "judge_rubric",
    }
)
_FRESHNESS_KEYS = frozenset({"as_of", "window_days", "revalidate_by", "drift_risk"})
_BUDGET_KEYS = ("max_provider_calls", "max_wall_clock_seconds", "max_total_tokens")
_PROVENANCE_KEYS = frozenset(
    {"status", "verification_method", "verified_at", "source_urls", "note"}
)
_TASK_RUBRIC_KEYS = frozenset({"rubric_id", "minimum_score"})
_VALIDATOR_KEYS = frozenset({"kind", "fields", "minimum_fields_correct"})
_RULE_KEYS = frozenset({"any_of", "none_of", "min_length", "require_https"})
_CHECK_KEYS: dict[str, frozenset[str]] = {
    "value_match": frozenset({"name", "check", "optional", "any_of", "none_of", "min_length"}),
    "member_set": frozenset(
        {
            "name",
            "check",
            "optional",
            "members",
            "forbidden_members",
            "min_members",
            "allow_extra",
            "match_field",
            "element_rules_all",
        }
    ),
    "citation_set": frozenset(
        {
            "name",
            "check",
            "optional",
            "min_citations",
            "min_distinct_hosts",
            "required_hosts",
            "allowed_hosts",
            "forbidden_hosts",
            "path_patterns",
            "require_https",
        }
    ),
    "table_cells": frozenset(
        {
            "name",
            "check",
            "optional",
            "row_key",
            "rows",
            "required_columns",
            "column_rules",
            "forbid_numeric_columns",
        }
    ),
    "decline": frozenset(
        {"name", "check", "optional", "any_of", "none_of", "min_length", "ignore_fields"}
    ),
}
_MEMBER_KEYS = frozenset({"name", "patterns", "element_rules"})
_TABLE_ROW_KEYS = frozenset({"key", "key_patterns", "cells"})


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------


def _catalog_dir(root: Path | None) -> Path:
    base = module_root() if root is None else root
    return base / "catalogs" / "production"


def load_production_catalog(root: Path | None = None) -> dict[str, Any]:
    """Load the production catalog and its rubric registry, unvalidated."""

    catalog_dir = _catalog_dir(root)
    catalog = _load_mapping(catalog_dir / "tasks.yaml", "production task catalog")
    rubrics_ref = catalog.get("rubrics_ref", "rubrics.yaml")
    if not isinstance(rubrics_ref, str) or "/" in rubrics_ref or rubrics_ref.startswith("."):
        raise SchemaError("production task catalog.rubrics_ref must be a plain file name")
    rubrics = _load_mapping(catalog_dir / rubrics_ref, "production rubric registry")
    return {"catalog": catalog, "rubrics": rubrics}


def _require_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchemaError(f"{label} must be a positive integer")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{label} must be a non-empty string")
    return value


def _require_pattern_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SchemaError(f"{label} must be a non-empty list of regexes")
    for pattern in value:
        _require_text(pattern, f"{label}[]")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SchemaError(f"{label} has an invalid regex {pattern!r}: {exc}") from exc
    return list(value)


def _require_host_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SchemaError(f"{label} must be a non-empty list of hosts")
    hosts = [_require_text(host, f"{label}[]").lower() for host in value]
    if len(set(hosts)) != len(hosts):
        raise SchemaError(f"{label} has duplicate hosts")
    return hosts


def _validate_rule(value: object, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise SchemaError(f"{label} must be a non-empty mapping")
    _reject_unknown(value, set(_RULE_KEYS), label)
    if "any_of" in value:
        _require_pattern_list(value["any_of"], f"{label}.any_of")
    if "none_of" in value:
        _require_pattern_list(value["none_of"], f"{label}.none_of")
    if "min_length" in value:
        _require_positive_int(value["min_length"], f"{label}.min_length")
    if "require_https" in value and not isinstance(value["require_https"], bool):
        raise SchemaError(f"{label}.require_https must be a boolean")


def _validate_rule_map(value: object, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise SchemaError(f"{label} must be a non-empty mapping")
    for column, rule in value.items():
        _require_text(column, f"{label} key")
        _validate_rule(rule, f"{label}.{column}")


def _validate_members(value: object, label: str, *, allow_empty: bool) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise SchemaError(f"{label} must be a non-empty list")
    names: set[str] = set()
    for member in value:
        if not isinstance(member, dict):
            raise SchemaError(f"{label}[] must be a mapping")
        _reject_unknown(member, set(_MEMBER_KEYS), f"{label}[]")
        name = _require_text(member.get("name"), f"{label}[].name")
        if name in names:
            raise SchemaError(f"{label} has duplicate member name: {name}")
        names.add(name)
        _require_pattern_list(member.get("patterns"), f"{label}[{name}].patterns")
        if "element_rules" in member:
            _validate_rule_map(member["element_rules"], f"{label}[{name}].element_rules")


def _validate_check(task_id: str, spec: object) -> tuple[str, str]:
    """Validate one ground-truth field check; return (field name, check kind)."""

    if not isinstance(spec, dict):
        raise SchemaError(f"task {task_id}.deterministic_validator.fields[] must be a mapping")
    name = _require_text(spec.get("name"), f"task {task_id} validator field name")
    label = f"task {task_id}.field[{name}]"
    check = spec.get("check")
    if check not in CHECK_KINDS:
        raise SchemaError(f"{label} has unknown check: {check!r}")
    _reject_unknown(spec, set(_CHECK_KEYS[check]), label)
    if "optional" in spec and not isinstance(spec["optional"], bool):
        raise SchemaError(f"{label}.optional must be a boolean")

    if check in {"value_match", "decline"}:
        _require_pattern_list(spec.get("any_of"), f"{label}.any_of")
        if "none_of" in spec:
            _require_pattern_list(spec["none_of"], f"{label}.none_of")
        if "min_length" in spec:
            _require_positive_int(spec["min_length"], f"{label}.min_length")
        if check == "decline" and "ignore_fields" in spec:
            ignore = spec["ignore_fields"]
            if not isinstance(ignore, list) or not ignore:
                raise SchemaError(f"{label}.ignore_fields must be a non-empty list")
            for field_name in ignore:
                _require_text(field_name, f"{label}.ignore_fields[]")
    elif check == "member_set":
        _validate_members(spec.get("members", []), f"{label}.members", allow_empty=True)
        if "forbidden_members" in spec:
            _validate_members(
                spec["forbidden_members"], f"{label}.forbidden_members", allow_empty=False
            )
        min_members = _require_positive_int(spec.get("min_members"), f"{label}.min_members")
        declared = len(spec.get("members") or [])
        if declared and min_members < declared:
            raise SchemaError(
                f"{label}.min_members ({min_members}) is below its {declared} required members, "
                "so the floor can never bind"
            )
        if not isinstance(spec.get("allow_extra"), bool):
            raise SchemaError(f"{label}.allow_extra must be a boolean")
        if "match_field" in spec:
            _require_text(spec["match_field"], f"{label}.match_field")
        if "element_rules_all" in spec:
            _validate_rule_map(spec["element_rules_all"], f"{label}.element_rules_all")
    elif check == "citation_set":
        _require_positive_int(spec.get("min_citations"), f"{label}.min_citations")
        if "min_distinct_hosts" in spec:
            _require_positive_int(spec["min_distinct_hosts"], f"{label}.min_distinct_hosts")
        for key in ("required_hosts", "allowed_hosts", "forbidden_hosts"):
            if key in spec:
                _require_host_list(spec[key], f"{label}.{key}")
        if "path_patterns" in spec:
            _require_pattern_list(spec["path_patterns"], f"{label}.path_patterns")
        if "require_https" in spec and not isinstance(spec["require_https"], bool):
            raise SchemaError(f"{label}.require_https must be a boolean")
        # The allowlist is read the way the scorer reads it: `allowed_hosts`
        # declares domain subtrees, not literal strings. An exact set
        # subtraction here would refuse a catalog the scorer accepts --
        # `required_hosts: [docs.python.org]` under `allowed_hosts:
        # [python.org]` is satisfied at scoring time by `_host_matches`, and a
        # preflight that rejects it is refusing a correct catalog for a
        # property it does not actually violate. Keep the direction strict: the
        # required host itself must fall inside a declared subtree, so a
        # required parent under a narrower allowlist (`[python.org]` allowed
        # only `[docs.python.org]`) is still the author error it always was.
        allowed = spec.get("allowed_hosts") or []
        required = spec.get("required_hosts") or []
        if allowed:
            excluded = sorted(
                host
                for host in required
                if not any(_host_matches(host, declared) for declared in allowed)
            )
            if excluded:
                raise SchemaError(
                    f"{label} requires host(s) its allowlist excludes: {', '.join(excluded)}"
                )
    else:  # table_cells
        _require_text(spec.get("row_key"), f"{label}.row_key")
        columns = spec.get("required_columns")
        if not isinstance(columns, list) or not columns:
            raise SchemaError(f"{label}.required_columns must be a non-empty list")
        for column in columns:
            _require_text(column, f"{label}.required_columns[]")
        rows = spec.get("rows")
        if not isinstance(rows, list) or not rows:
            raise SchemaError(f"{label}.rows must be a non-empty list")
        keys: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise SchemaError(f"{label}.rows[] must be a mapping")
            _reject_unknown(row, set(_TABLE_ROW_KEYS), f"{label}.rows[]")
            key = _require_text(row.get("key"), f"{label}.rows[].key")
            if key in keys:
                raise SchemaError(f"{label} has duplicate row key: {key}")
            keys.add(key)
            _require_pattern_list(row.get("key_patterns"), f"{label}.rows[{key}].key_patterns")
            if "cells" in row:
                _validate_rule_map(row["cells"], f"{label}.rows[{key}].cells")
                unknown = sorted(set(row["cells"]) - set(columns))
                if unknown:
                    raise SchemaError(
                        f"{label}.rows[{key}].cells names non-required column(s): "
                        f"{', '.join(unknown)}"
                    )
        if "column_rules" in spec:
            _validate_rule_map(spec["column_rules"], f"{label}.column_rules")
            unknown = sorted(set(spec["column_rules"]) - set(columns))
            if unknown:
                raise SchemaError(
                    f"{label}.column_rules names non-required column(s): {', '.join(unknown)}"
                )
        if "forbid_numeric_columns" in spec:
            forbidden = spec["forbid_numeric_columns"]
            if not isinstance(forbidden, list) or not forbidden:
                raise SchemaError(f"{label}.forbid_numeric_columns must be a non-empty list")
            unknown = sorted({str(column) for column in forbidden} - set(columns))
            if unknown:
                raise SchemaError(
                    f"{label}.forbid_numeric_columns names non-required column(s): "
                    f"{', '.join(unknown)}"
                )
    return name, check


def _validate_freshness(task_id: str, value: object) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"task {task_id}.freshness must be a mapping")
    _reject_unknown(value, set(_FRESHNESS_KEYS), f"task {task_id}.freshness")
    for key in ("as_of", "revalidate_by"):
        _validate_iso_date(value.get(key), f"task {task_id}.freshness.{key}")
    window_days = _require_positive_int(
        value.get("window_days"), f"task {task_id}.freshness.window_days"
    )
    if value.get("drift_risk") not in DRIFT_RISKS:
        raise SchemaError(
            f"task {task_id}.freshness.drift_risk must be one of {sorted(DRIFT_RISKS)}"
        )
    as_of = date.fromisoformat(value["as_of"])
    revalidate_by = date.fromisoformat(value["revalidate_by"])
    # The window is the load-bearing half of freshness: if `revalidate_by` can
    # drift away from `as_of + window_days`, a task can look freshly windowed
    # while its real deadline is years out, and `publication_blockers` stops
    # being able to refuse stale truth.
    if (revalidate_by - as_of).days != window_days:
        raise SchemaError(
            f"task {task_id}.freshness.revalidate_by must equal as_of plus window_days "
            f"({window_days} days after {value['as_of']})"
        )


def _validate_budgets(task_id: str, value: object, ceilings: Mapping[str, int]) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"task {task_id}.budgets must be a mapping")
    _reject_unknown(value, set(_BUDGET_KEYS), f"task {task_id}.budgets")
    for key in _BUDGET_KEYS:
        budget = _require_positive_int(value.get(key), f"task {task_id}.budgets.{key}")
        if budget > ceilings[key]:
            raise SchemaError(
                f"task {task_id}.budgets.{key} ({budget}) exceeds the suite ceiling "
                f"({ceilings[key]})"
            )


def _validate_provenance(task_id: str, value: object, *, deterministic: bool) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"task {task_id}.truth_provenance must be a mapping")
    _reject_unknown(value, set(_PROVENANCE_KEYS), f"task {task_id}.truth_provenance")
    status = value.get("status")
    if status not in TRUTH_STATUSES:
        raise SchemaError(
            f"task {task_id}.truth_provenance.status must be one of {sorted(TRUTH_STATUSES)}"
        )
    if deterministic and status == "none":
        raise SchemaError(
            f"task {task_id} declares per-field ground truth, so its truth_provenance.status "
            "cannot be none"
        )
    if not deterministic and status != "none":
        raise SchemaError(
            f"task {task_id} is rubric-scored, so its truth_provenance.status must be none"
        )
    if status == "none":
        _require_text(value.get("note"), f"task {task_id}.truth_provenance.note")
        for key in ("verification_method", "verified_at"):
            if key in value:
                raise SchemaError(
                    f"task {task_id}.truth_provenance.{key} is meaningless without pinned truth"
                )
        return
    if value.get("verification_method") not in VERIFICATION_METHODS:
        raise SchemaError(
            f"task {task_id}.truth_provenance.verification_method must be one of "
            f"{sorted(VERIFICATION_METHODS)}"
        )
    _validate_iso_date(value.get("verified_at"), f"task {task_id}.truth_provenance.verified_at")
    source_urls = value.get("source_urls")
    if not isinstance(source_urls, list) or not source_urls:
        raise SchemaError(f"task {task_id}.truth_provenance.source_urls must be non-empty")
    for url in source_urls:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise SchemaError(f"task {task_id}.truth_provenance.source_urls[] must use https")
    # Live truth rots. Requiring a note makes the author say what would make
    # the task wrong, which is the only thing that lets a later operator judge
    # whether re-verification is still needed.
    if status == "current_state":
        _require_text(value.get("note"), f"task {task_id}.truth_provenance.note")


def _validate_deliverable_schema(task_id: str, value: object, evidence_field: str) -> list[str]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise SchemaError(f"task {task_id}.deliverable_schema must be an object schema")
    required = value.get("required")
    if not isinstance(required, list) or len(required) < 2:
        raise SchemaError(
            f"task {task_id}.deliverable_schema.required needs at least two fields; a "
            "single-field deliverable is a fact lookup, which is what this catalog replaced"
        )
    properties = value.get("properties")
    if not isinstance(properties, dict):
        raise SchemaError(f"task {task_id}.deliverable_schema.properties must be a mapping")
    missing = sorted(set(required) - set(properties))
    if missing:
        raise SchemaError(
            f"task {task_id}.deliverable_schema.required names undefined propert(ies): "
            f"{', '.join(missing)}"
        )
    if evidence_field not in required:
        raise SchemaError(
            f"task {task_id}.evidence_field {evidence_field!r} must be a required deliverable "
            "field, so an ungrounded deliverable cannot score as complete"
        )
    evidence_schema = properties[evidence_field]
    if not isinstance(evidence_schema, dict) or evidence_schema.get("type") != "array":
        raise SchemaError(f"task {task_id}.evidence_field must be an array of citation URLs")
    return [str(name) for name in required]


def _validate_rubrics(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    _reject_unknown(dict(registry), set(_RUBRIC_REGISTRY_KEYS), "production rubric registry")
    if registry.get("schema_version") != 1:
        raise SchemaError("production rubric registry requires schema_version 1")
    if registry.get("registry_id") != "production-rubrics-v1":
        raise SchemaError("production rubric registry.registry_id must be production-rubrics-v1")
    _validate_iso_date(registry.get("version"), "production rubric registry.version")
    rubrics = registry.get("rubrics")
    if not isinstance(rubrics, list) or not rubrics:
        raise SchemaError("production rubric registry.rubrics must be a non-empty list")
    by_id: dict[str, dict[str, Any]] = {}
    for rubric in rubrics:
        if not isinstance(rubric, dict):
            raise SchemaError("production rubric registry.rubrics[] must be a mapping")
        _reject_unknown(rubric, set(_RUBRIC_KEYS), "production rubric registry.rubrics[]")
        rubric_id = _require_text(rubric.get("rubric_id"), "rubric.rubric_id")
        if rubric_id in by_id:
            raise SchemaError(f"duplicate rubric_id: {rubric_id}")
        for key in ("description", "blinding_note"):
            _require_text(rubric.get(key), f"rubric {rubric_id}.{key}")
        dimensions = rubric.get("dimensions")
        if not isinstance(dimensions, list) or len(dimensions) < 2:
            raise SchemaError(
                f"rubric {rubric_id}.dimensions needs at least two dimensions; a one-dimension "
                "rubric is a holistic score and hides which dimension failed"
            )
        for dimension in dimensions:
            _require_text(dimension, f"rubric {rubric_id}.dimensions[]")
        if len(set(dimensions)) != len(dimensions):
            raise SchemaError(f"rubric {rubric_id}.dimensions has duplicates")
        scale = rubric.get("score_scale")
        if not isinstance(scale, dict):
            raise SchemaError(f"rubric {rubric_id}.score_scale must be a mapping")
        _reject_unknown(scale, {"min", "max"}, f"rubric {rubric_id}.score_scale")
        low, high = scale.get("min"), scale.get("max")
        if not isinstance(low, int | float) or not isinstance(high, int | float) or low >= high:
            raise SchemaError(f"rubric {rubric_id}.score_scale needs numeric min < max")
        minimum = rubric.get("minimum_score")
        if not isinstance(minimum, int | float) or not low <= minimum <= high:
            raise SchemaError(f"rubric {rubric_id}.minimum_score must fit score_scale")
        by_id[rubric_id] = dict(rubric)
    return by_id


def _validate_task_rubric(
    task_id: str, value: object, rubrics: Mapping[str, Mapping[str, Any]]
) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"task {task_id}.judge_rubric must be a mapping")
    _reject_unknown(value, set(_TASK_RUBRIC_KEYS), f"task {task_id}.judge_rubric")
    rubric_id = value.get("rubric_id")
    if rubric_id not in rubrics:
        raise SchemaError(f"task {task_id} has unknown judge_rubric.rubric_id: {rubric_id!r}")
    if "minimum_score" in value:
        rubric = rubrics[str(rubric_id)]
        scale = rubric["score_scale"]
        minimum = value["minimum_score"]
        if not isinstance(minimum, int | float) or not scale["min"] <= minimum <= scale["max"]:
            raise SchemaError(
                f"task {task_id}.judge_rubric.minimum_score must fit the {rubric_id} score_scale"
            )


def validate_production_catalog(root: Path | None = None) -> list[str]:
    """Validate the WSB production catalog offline; return its task ids."""

    loaded = load_production_catalog(root)
    catalog, rubric_registry = loaded["catalog"], loaded["rubrics"]
    rubrics = _validate_rubrics(rubric_registry)

    _reject_unknown(catalog, set(_CATALOG_KEYS), "production task catalog")
    if catalog.get("schema_version") != 1:
        raise SchemaError("production task catalog requires schema_version 1")
    if catalog.get("catalog_id") != "production-v1":
        raise SchemaError("production task catalog.catalog_id must be production-v1")
    if catalog.get("suite") != "production":
        raise SchemaError("production task catalog.suite must be production")
    _validate_iso_date(catalog.get("version"), "production task catalog.version")
    for key in ("description", "authoring_policy"):
        _require_text(catalog.get(key), f"production task catalog.{key}")

    ceilings = catalog.get("budget_ceilings")
    if not isinstance(ceilings, dict):
        raise SchemaError("production task catalog.budget_ceilings must be a mapping")
    _reject_unknown(ceilings, set(_BUDGET_KEYS), "production task catalog.budget_ceilings")
    ceiling_values = {
        key: _require_positive_int(ceilings.get(key), f"budget_ceilings.{key}")
        for key in _BUDGET_KEYS
    }

    declared_classes = catalog.get("task_classes")
    if not isinstance(declared_classes, dict):
        raise SchemaError("production task catalog.task_classes must be a mapping")
    if set(declared_classes) != set(CLASS_VALIDATOR_KINDS):
        raise SchemaError(
            "production task catalog.task_classes must declare exactly "
            f"{sorted(CLASS_VALIDATOR_KINDS)}"
        )
    for task_class, description in declared_classes.items():
        _require_text(description, f"task_classes.{task_class}")

    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SchemaError("production task catalog.tasks must be a non-empty list")

    task_ids: list[str] = []
    class_counts: dict[str, int] = {}
    expected_fail: list[str] = []
    expected_fail_classes: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise SchemaError("production task catalog.tasks[] must be a mapping")
        _reject_unknown(task, set(_TASK_KEYS), "production task catalog.tasks[]")
        task_id = _require_text(task.get("id"), "production task id")
        if task_id in task_ids:
            raise SchemaError(f"duplicate production task id: {task_id}")
        task_class = task.get("task_class")
        if task_class not in CLASS_VALIDATOR_KINDS:
            raise SchemaError(f"task {task_id} has unknown task_class: {task_class!r}")
        _require_text(task.get("prompt"), f"task {task_id}.prompt")
        _validate_freshness(task_id, task.get("freshness"))
        _validate_budgets(task_id, task.get("budgets"), ceiling_values)
        evidence_field = _require_text(task.get("evidence_field"), f"task {task_id}.evidence_field")
        required_fields = _validate_deliverable_schema(
            task_id, task.get("deliverable_schema"), evidence_field
        )

        instruments = [key for key in ("deterministic_validator", "judge_rubric") if key in task]
        if len(instruments) != 1:
            raise SchemaError(
                f"task {task_id} must declare exactly one validator or rubric, never neither "
                f"and never both; found {instruments or ['none']}"
            )
        deterministic = instruments[0] == "deterministic_validator"
        if deterministic:
            _validate_validator(
                task_id, str(task_class), task["deterministic_validator"], required_fields
            )
        else:
            _validate_task_rubric(task_id, task["judge_rubric"], rubrics)
        _validate_provenance(task_id, task.get("truth_provenance"), deterministic=deterministic)

        outcome = task.get("expected_outcome")
        if outcome not in EXPECTED_OUTCOMES:
            raise SchemaError(
                f"task {task_id}.expected_outcome must be one of {sorted(EXPECTED_OUTCOMES)}"
            )
        if outcome == "expected_fail":
            _require_text(task.get("expected_fail_reason"), f"task {task_id}.expected_fail_reason")
            expected_fail.append(task_id)
            expected_fail_classes.add(str(task_class))
        elif "expected_fail_reason" in task:
            raise SchemaError(
                f"task {task_id} declares an expected_fail_reason but is not expected_fail"
            )

        task_ids.append(task_id)
        class_counts[str(task_class)] = class_counts.get(str(task_class), 0) + 1

    off_quota = sorted(
        f"{name}={class_counts.get(name, 0)}"
        for name in CLASS_VALIDATOR_KINDS
        if class_counts.get(name, 0) != TASKS_PER_CLASS
    )
    if off_quota:
        raise SchemaError(
            f"production catalog requires exactly {TASKS_PER_CLASS} tasks per class; got "
            f"{', '.join(off_quota)}"
        )
    if len(expected_fail) < MIN_EXPECTED_FAIL_TASKS:
        raise SchemaError(
            f"production catalog requires at least {MIN_EXPECTED_FAIL_TASKS} expected-fail tasks "
            f"for measurement headroom; got {len(expected_fail)}"
        )
    # Headroom concentrated in one class is not headroom: the suite reports per
    # class, so three expected-fail tasks inside `unanswerable` would leave
    # every other class free to saturate exactly as L2 did.
    if len(expected_fail_classes) < MIN_EXPECTED_FAIL_CLASSES:
        raise SchemaError(
            "production catalog requires expected-fail tasks spread across at least "
            f"{MIN_EXPECTED_FAIL_CLASSES} classes; got {sorted(expected_fail_classes)}"
        )
    return task_ids


def _validate_validator(
    task_id: str, task_class: str, value: object, required_fields: Sequence[str]
) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"task {task_id}.deterministic_validator must be a mapping")
    _reject_unknown(value, set(_VALIDATOR_KEYS), f"task {task_id}.deterministic_validator")
    kind = value.get("kind")
    if kind not in VALIDATOR_KINDS:
        raise SchemaError(f"task {task_id} has unknown deterministic_validator.kind: {kind!r}")
    allowed = CLASS_VALIDATOR_KINDS[task_class]
    if kind not in allowed:
        raise SchemaError(
            f"task {task_id} is a {task_class} task, so its validator kind must be one of "
            f"{sorted(allowed)}; got {kind!r}"
        )
    fields = value.get("fields")
    if not isinstance(fields, list) or not fields:
        raise SchemaError(f"task {task_id}.deterministic_validator.fields must be non-empty")
    names: list[str] = []
    checks: list[str] = []
    for spec in fields:
        name, check = _validate_check(task_id, spec)
        if name in names:
            raise SchemaError(f"task {task_id} scores field {name!r} twice")
        names.append(name)
        checks.append(check)
    unknown = sorted(set(names) - set(required_fields))
    if unknown:
        raise SchemaError(
            f"task {task_id} scores field(s) its deliverable_schema does not require: "
            f"{', '.join(unknown)}"
        )
    # Every required field must be scored. A required-but-unscored field is a
    # free pass: the arm has to produce it for schema validity and nothing ever
    # checks what it put there.
    unscored = sorted(set(required_fields) - set(names))
    if unscored:
        raise SchemaError(
            f"task {task_id} leaves required deliverable field(s) unscored: {', '.join(unscored)}"
        )
    if not set(checks) & STRUCTURAL_CHECKS:
        raise SchemaError(
            f"task {task_id} is scored entirely by scalar pattern matches; a production task "
            f"needs at least one of {sorted(STRUCTURAL_CHECKS)} so it cannot be satisfied by a "
            "single regex-matchable token"
        )
    if "minimum_fields_correct" in value:
        minimum = _require_positive_int(
            value["minimum_fields_correct"],
            f"task {task_id}.deterministic_validator.minimum_fields_correct",
        )
        if minimum > len(names):
            raise SchemaError(
                f"task {task_id}.deterministic_validator.minimum_fields_correct ({minimum}) "
                f"exceeds its {len(names)} scored fields"
            )


# --------------------------------------------------------------------------
# Publication readiness
# --------------------------------------------------------------------------


def publication_blockers(
    catalog: Mapping[str, Any], *, today: date | None = None
) -> list[dict[str, str]]:
    """Return every reason the catalog is not ready to carry published claims.

    Two conditions block. Truth recorded as ``author_assertion`` has never been
    re-fetched, so quoting accuracy against it quotes the author's memory.
    ``current_state`` truth past its ``revalidate_by`` has aged out of the
    window the task itself declared. Both are advisory here -- WSB-07 and
    WSB-09 decide what to do with them -- but they are computed from the
    catalog so no run has to guess.
    """

    now = date.today() if today is None else today
    blockers: list[dict[str, str]] = []
    for task in catalog.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id"))
        provenance = task.get("truth_provenance") or {}
        freshness = task.get("freshness") or {}
        if not isinstance(provenance, Mapping) or not isinstance(freshness, Mapping):
            continue
        if provenance.get("verification_method") == "author_assertion":
            blockers.append(
                {
                    "task_id": task_id,
                    "reason": "truth_unverified",
                    "detail": "ground truth was asserted by the catalog author and not re-fetched",
                }
            )
        revalidate_by = freshness.get("revalidate_by")
        if (
            provenance.get("status") == "current_state"
            and isinstance(revalidate_by, str)
            and date.fromisoformat(revalidate_by) < now
        ):
            blockers.append(
                {
                    "task_id": task_id,
                    "reason": "freshness_window_expired",
                    "detail": f"live-condition truth was due for re-verification by {revalidate_by}",
                }
            )
    return blockers


__all__ = [
    "CHECK_KINDS",
    "CLASS_VALIDATOR_KINDS",
    "MIN_EXPECTED_FAIL_CLASSES",
    "MIN_EXPECTED_FAIL_TASKS",
    "TASKS_PER_CLASS",
    "VALIDATOR_KINDS",
    "load_production_catalog",
    "publication_blockers",
    "score_deliverable",
    "suspect_ground_truth_fields",
    "validate_production_catalog",
]
