"""Catalog loading helpers for SEW suites."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .schema import (
    SchemaError,
    load_suite_manifest,
    load_task_manifest,
    validate_artifact_path,
)

_CLAIM_REGISTRY_KEYS = {
    "schema_version",
    "registry_id",
    "version",
    "claims",
}
_CLAIM_KEYS = {
    "hypothesis_id",
    "claimant",
    "domain",
    "claim",
    "source_url",
    "supporting_source_url",
    "retrieved_at",
    "falsifiable_hypothesis",
    "claim_strength",
}
# How strongly the vendor's OWN source supports the hypothesis derived from it.
# The registry's `claim` text is frequently a capability description ("ships a
# GitHub workflow"), while `falsifiable_hypothesis` asserts head-to-head
# superiority. Recording which one the source actually makes keeps a later
# "H1 REFUTED" from attributing to a vendor a performance claim its own page
# never made -- the "results become vendor marketing" risk in SPEC section 6,
# which cuts in both directions.
_CLAIM_STRENGTHS = {
    # The vendor explicitly claims to beat competitors in this domain.
    "vendor_asserts_superiority",
    # The vendor ships/documents a domain capability but makes no comparative
    # claim. A refutation here refutes OUR derived hypothesis, not the vendor.
    "vendor_ships_domain_surface",
}
_DOMAIN_CATALOG_KEYS = {
    "schema_version",
    "catalog_id",
    "version",
    "description",
    "authoring_policy",
    "execution_contract",
    "tasks",
}
_DOMAIN_TASK_KEYS = {
    "id",
    "domain",
    "hypothesis_id",
    "prompt",
    "deliverable_schema",
    "deterministic_validator",
    "judge_rubric",
    "expected_claimant_outcome",
    "expected_loss_reason",
    # Per-task context the declared vendor surfaces need to engage. Without
    # this seam Parallel's four declarations (source_policy for code_and_pr and
    # legal, entity_type/match_conditions for entity_resolution and gtm) can
    # never receive their context, so Parallel resolves generic in all four
    # domains while Firecrawl and Exa get specialist arms -- the exact
    # asymmetry DSB exists to prevent ("comparing a specialist surface against
    # a generic one rigs the comparison"). Keys are vendor-neutral and
    # validated below.
    "surface_context",
}
SURFACE_CONTEXT_KEYS = {
    "source_policy",
    # Parallel's FindAll entity vocabulary (free-form vendor text).
    "entity_type",
    # Exa's category vocabulary (a closed set). Deliberately a separate key:
    # one shared `entity_type` made a company task resolve to Exa's *people*
    # index while looking correct.
    "exa_category",
    "match_conditions",
    "match_limit",
    "generator",
}
_VALIDATOR_KEYS = {
    "kind",
    "allow_honest_ambiguity",
    "enforce_window",
    "expected_accession_number",
    "expected_effective_date",
    "expected_independent_claims",
    "expected_match_status",
    "expected_status",
    "fail_confident_unsupported_answer",
    "fail_fabricated_entities",
    "fail_fabricated_people",
    "forbid_personal_contact_details",
    "minimum_call_sites",
    "minimum_entities",
    "require_accession_number",
    "require_all_independent_claims",
    "require_change_reference",
    "require_closed_issue",
    "require_comment_anchor",
    "require_commit_pins",
    "require_current_role_evidence",
    "require_dated_primary_evidence",
    "require_disambiguating_evidence",
    "require_distinct_identifiers",
    "require_domain_match",
    "require_effective_date",
    "require_empty_accounts",
    "require_field_citations",
    "require_identifier_match",
    "require_issue",
    "require_merged_pr",
    "require_no_personal_data",
    "require_official_code_source",
    "require_release_tag",
    "require_repository_match",
    "require_source_dates",
    "required_fields",
    "required_host",
    "required_identifier_count",
    "required_primary_domain",
    "reject_superseded_text",
    # Trusted bounds for `enforce_window`. The window must come from the task
    # binding, never from the answer; an answer that supplied its own bounds
    # would self-authenticate every signal it returned.
    "window_start",
    "window_end",
}
_VALIDATOR_KINDS = {
    "canonical_company",
    "cited_firmographics",
    "corporate_identity_chain",
    "current_statute",
    "dated_buying_signals",
    "entity_disambiguation",
    "github_reference",
    "honest_empty_result",
    "patent_claims",
    "privacy_decline",
    "public_role_holder",
    "release_lineage",
    "repository_source_locations",
    "sanctions_record",
    "sec_filing",
    "supported_decline",
}
_RUBRIC_KEYS = {
    "rubric_id",
    "minimum_score",
    "score_scale",
    "dimensions",
    "secondary_source_penalty",
    "fabricated_entity_score",
}
_RUBRIC_IDS = {
    "wsb02-domain-evidence-v1",
    "wsb02-entity-resolution-v1",
    "wsb02-gtm-evidence-v1",
    "wsb02-legal-primary-source-v1",
}


def module_root() -> Path:
    source = Path(__file__).resolve().parents[3]
    if (source / "catalogs").is_dir():
        return source
    return Path(__file__).resolve().parent / "_data"


def validate_lighthouse_catalog(root: Path | None = None) -> list[str]:
    base = module_root() if root is None else root
    suite = load_suite_manifest(base / "catalogs" / "lighthouse")
    task_ids = []
    for task_id in suite["tasks"]:
        validate_artifact_path(task_id, "suite manifest.tasks[]")
        task = load_task_manifest(base / "tasks" / task_id)
        if task["task_id"] != task_id:
            raise SchemaError(f"task manifest id mismatch for {task_id}")
        task_ids.append(task_id)
    return task_ids


def _load_mapping(path: Path, label: str) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SchemaError(f"cannot load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be a mapping: {path}")
    return value


def _reject_unknown(value: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SchemaError(f"{label} has unknown key(s): {', '.join(unknown)}")


def _validate_iso_date(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{label} must be a non-empty ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise SchemaError(f"{label} must be an ISO date") from exc


def _validate_validator(task_id: str, value: dict) -> None:
    _reject_unknown(value, _VALIDATOR_KEYS, f"task {task_id}.deterministic_validator")
    kind = value.get("kind")
    if kind not in _VALIDATOR_KINDS:
        raise SchemaError(f"task {task_id} has unknown deterministic_validator.kind: {kind}")


def _validate_rubric(task_id: str, value: dict) -> None:
    _reject_unknown(value, _RUBRIC_KEYS, f"task {task_id}.judge_rubric")
    rubric_id = value.get("rubric_id")
    if rubric_id not in _RUBRIC_IDS:
        raise SchemaError(f"task {task_id} has unknown judge_rubric.rubric_id: {rubric_id}")
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise SchemaError(f"task {task_id}.judge_rubric.dimensions must be non-empty")
    if not all(isinstance(dimension, str) and dimension.strip() for dimension in dimensions):
        raise SchemaError(f"task {task_id}.judge_rubric.dimensions must be strings")
    minimum_score = value.get("minimum_score")
    if not isinstance(minimum_score, int | float):
        raise SchemaError(f"task {task_id}.judge_rubric.minimum_score must be numeric")
    score_scale = value.get("score_scale")
    if not isinstance(score_scale, dict):
        raise SchemaError(f"task {task_id}.judge_rubric.score_scale must be a mapping")
    _reject_unknown(score_scale, {"min", "max"}, f"task {task_id}.judge_rubric.score_scale")
    if not isinstance(score_scale.get("min"), int | float) or not isinstance(
        score_scale.get("max"), int | float
    ):
        raise SchemaError(f"task {task_id}.judge_rubric.score_scale min/max must be numeric")
    if not score_scale["min"] <= minimum_score <= score_scale["max"]:
        raise SchemaError(f"task {task_id}.judge_rubric.minimum_score must fit score_scale")


def load_domain_tasks(root: Path | None = None) -> dict[str, dict]:
    """Validate the domain catalog and return its task mappings keyed by id.

    Per-domain suites (DSB-03 through DSB-06) bind run instances onto these
    task types and need the declared ``deliverable_schema`` and
    validator/rubric contract. They read it from here rather than re-parsing
    the catalog with a second, looser set of rules, so a suite cannot drift
    onto a task-shaped mapping of its own.

    The catalog is validated first -- every consumer then sees exactly what
    the operator gate sees -- and the shape of each record is still checked
    here, because this returns the mappings themselves and a caller indexing
    `task["id"]` deserves a named error rather than a KeyError.
    """
    catalog, _ = _validated_domain_catalog(root)
    tasks = catalog.get("tasks")
    if not isinstance(tasks, list):
        raise SchemaError("domain task catalog.tasks must be a list")
    records: dict[str, dict] = {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise SchemaError("domain task catalog.tasks[] must be a mapping with an id")
        records[task["id"]] = task
    return records


def validate_domain_catalog(root: Path | None = None) -> list[str]:
    """Validate the DSB task and claim catalogs without network access."""

    return _validated_domain_catalog(root)[1]


def _validated_domain_catalog(root: Path | None = None) -> tuple[dict, list[str]]:
    """Parse and validate the domain catalogs once; return the task catalog too.

    ``load_domain_tasks`` needs the very document the gate just validated, so
    it takes it from here instead of re-reading and re-parsing tasks.yaml --
    one parse, and no window in which the two reads could see different files.
    """
    base = module_root() if root is None else root
    catalog_dir = base / "catalogs" / "domains"
    catalog = _load_mapping(catalog_dir / "tasks.yaml", "domain task catalog")
    registry = _load_mapping(catalog_dir / "claims.yaml", "domain claim registry")

    _reject_unknown(catalog, _DOMAIN_CATALOG_KEYS, "domain task catalog")
    _reject_unknown(registry, _CLAIM_REGISTRY_KEYS, "domain claim registry")
    if catalog.get("schema_version") != 1 or registry.get("schema_version") != 1:
        raise SchemaError("domain catalogs require schema_version 1")
    if catalog.get("catalog_id") != "domain-strengths-v1":
        raise SchemaError("domain task catalog.catalog_id must be domain-strengths-v1")
    if registry.get("registry_id") != "domain-strength-claims-v1":
        raise SchemaError("domain claim registry.registry_id must be domain-strength-claims-v1")
    _validate_iso_date(catalog.get("version"), "domain task catalog.version")
    _validate_iso_date(registry.get("version"), "domain claim registry.version")
    if (
        not isinstance(catalog.get("execution_contract"), str)
        or not catalog["execution_contract"].strip()
    ):
        raise SchemaError("domain task catalog.execution_contract must be a non-empty string")

    hypotheses = registry.get("claims")
    if not isinstance(hypotheses, list) or not hypotheses:
        raise SchemaError("domain claim registry.claims must be a non-empty list")
    # Join the registry's claimant vocabulary onto provider ids here, in the
    # operator gate, not only in pytest: DSB-07/08 must join claim -> provider
    # -> surface, and `parallel` vs `parallel-web` would otherwise fail
    # silently at report time while `sew validate-domain-catalog` reports
    # green.
    try:
        from .domain_surfaces import DomainSurfaceError, assert_claim_registry_vocabulary

        assert_claim_registry_vocabulary([claim for claim in hypotheses if isinstance(claim, dict)])
    except DomainSurfaceError as exc:
        raise SchemaError(f"domain claim registry vocabulary: {exc}") from exc
    claim_by_id = {}
    for claim in hypotheses:
        if not isinstance(claim, dict):
            raise SchemaError("domain claim registry.claims[] must be a mapping")
        _reject_unknown(claim, _CLAIM_KEYS, "domain claim registry.claims[]")
        hypothesis_id = claim.get("hypothesis_id")
        if not isinstance(hypothesis_id, str) or not hypothesis_id:
            raise SchemaError("claim hypothesis_id must be a non-empty string")
        if hypothesis_id in claim_by_id:
            raise SchemaError(f"duplicate hypothesis_id: {hypothesis_id}")
        for key in (
            "claimant",
            "domain",
            "claim",
            "source_url",
            "retrieved_at",
            "falsifiable_hypothesis",
        ):
            if not isinstance(claim.get(key), str) or not claim[key].strip():
                raise SchemaError(f"claim {hypothesis_id}.{key} must be a non-empty string")
        if not claim["source_url"].startswith("https://"):
            raise SchemaError(f"claim {hypothesis_id}.source_url must use https")
        claim_strength = claim.get("claim_strength")
        if claim_strength not in _CLAIM_STRENGTHS:
            raise SchemaError(
                f"claim {hypothesis_id}.claim_strength must be one of "
                f"{sorted(_CLAIM_STRENGTHS)}; got {claim_strength!r}"
            )
        supporting_source_url = claim.get("supporting_source_url")
        if supporting_source_url is not None and (
            not isinstance(supporting_source_url, str)
            or not supporting_source_url.startswith("https://")
        ):
            raise SchemaError(f"claim {hypothesis_id}.supporting_source_url must use https")
        _validate_iso_date(claim["retrieved_at"], f"claim {hypothesis_id}.retrieved_at")
        claim_by_id[hypothesis_id] = claim
    if set(claim_by_id) != {"H1", "H2", "H3", "H4"}:
        raise SchemaError("domain claim registry must define exactly H1 through H4")

    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SchemaError("domain task catalog.tasks must be a non-empty list")
    task_ids: list[str] = []
    domain_counts: dict[str, int] = {}
    expected_loss_domains: set[str] = set()
    task_surface_contexts: list[tuple[str, str, dict | None]] = []
    for task in tasks:
        if not isinstance(task, dict):
            raise SchemaError("domain task catalog.tasks[] must be a mapping")
        _reject_unknown(task, _DOMAIN_TASK_KEYS, "domain task catalog.tasks[]")
        surface_context = task.get("surface_context")
        if surface_context is not None:
            if not isinstance(surface_context, dict):
                raise SchemaError("domain task surface_context must be a mapping")
            _reject_unknown(surface_context, SURFACE_CONTEXT_KEYS, "domain task surface_context")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise SchemaError("domain task id must be a non-empty string")
        if task_id in task_ids:
            raise SchemaError(f"duplicate domain task id: {task_id}")
        hypothesis_id = task.get("hypothesis_id")
        if hypothesis_id not in claim_by_id:
            raise SchemaError(f"task {task_id} has unknown hypothesis_id: {hypothesis_id}")
        domain = task.get("domain")
        if domain != claim_by_id[hypothesis_id]["domain"]:
            raise SchemaError(f"task {task_id} domain does not match {hypothesis_id}")
        if not isinstance(task.get("prompt"), str) or not task["prompt"].strip():
            raise SchemaError(f"task {task_id}.prompt must be a non-empty string")
        output_schema = task.get("deliverable_schema")
        if not isinstance(output_schema, dict) or output_schema.get("type") != "object":
            raise SchemaError(f"task {task_id}.deliverable_schema must be an object schema")
        if not isinstance(output_schema.get("required"), list) or not output_schema["required"]:
            raise SchemaError(f"task {task_id}.deliverable_schema.required must be non-empty")
        properties = output_schema.get("properties")
        if not isinstance(properties, dict) or not set(output_schema["required"]) <= set(
            properties
        ):
            raise SchemaError(f"task {task_id}.deliverable_schema must define required properties")
        evaluators = [key for key in ("deterministic_validator", "judge_rubric") if key in task]
        if (
            len(evaluators) != 1
            or not isinstance(task[evaluators[0]], dict)
            or not task[evaluators[0]]
        ):
            raise SchemaError(f"task {task_id} must declare exactly one validator or rubric")
        if evaluators[0] == "deterministic_validator":
            _validate_validator(task_id, task["deterministic_validator"])
        else:
            _validate_rubric(task_id, task["judge_rubric"])
        expected_outcome = task.get("expected_claimant_outcome", "competitive")
        if expected_outcome not in {"competitive", "loss"}:
            raise SchemaError(f"task {task_id} has invalid expected_claimant_outcome")
        if expected_outcome == "loss":
            if (
                not isinstance(task.get("expected_loss_reason"), str)
                or not task["expected_loss_reason"].strip()
            ):
                raise SchemaError(f"task {task_id} expected loss requires a reason")
            expected_loss_domains.add(domain)
        task_ids.append(task_id)
        task_surface_contexts.append((task_id, domain, surface_context))
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    expected_domains = {claim["domain"] for claim in hypotheses}
    if set(domain_counts) != expected_domains or any(
        count != 6 for count in domain_counts.values()
    ):
        raise SchemaError("domain task catalog requires exactly six tasks per claimed domain")
    if expected_loss_domains != expected_domains:
        raise SchemaError("every claimed domain requires an expected-loss task")

    # A task that cannot satisfy a vendor's declared surface context resolves
    # that vendor to the generic arm while its competitors reach their
    # specialist surfaces. H2's claimant would then run generic in its own
    # claimed domain -- the comparison is decided before a single query runs,
    # and the `generic_surface` marking makes the result look honest anyway.
    # Refuse at validation time rather than discover it in a report.
    from .domain_surfaces import unsatisfied_surface_context

    rigged: list[str] = []
    for task_id, domain, surface_context in task_surface_contexts:
        for detail in unsatisfied_surface_context(domain, surface_context):
            rigged.append(f"task {task_id}: {detail}")
    if rigged:
        raise SchemaError(
            "domain tasks cannot satisfy a declared vendor surface, so that vendor would "
            "run generic against specialist competitors: " + "; ".join(sorted(rigged)[:6])
        )
    return catalog, task_ids


__all__ = [
    "SURFACE_CONTEXT_KEYS",
    "load_domain_tasks",
    "module_root",
    "validate_domain_catalog",
    "validate_lighthouse_catalog",
]
