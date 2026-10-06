"""DSB-05 entity resolution domain suite: run instances and executable scoring.

The DSB-01 catalog declares six ``entity_resolution`` task *types* and the
evaluator contract each one carries. This module supplies the other half: the
run instances in ``catalogs/domains/entity_resolution.yaml``, and the executable
validators and judge bindings those contracts name.

Three properties are load-bearing and are checked rather than assumed.

* **Canonical identifiers are checked as identifiers.** A company answer is
  compared against a pinned registry identifier (an SEC CIK) and a registrable
  canonical domain, not against a name. Identifier namespaces are validated for
  well-formedness, and ORCID and ISNI are checked against their mod-11-2 check
  digit, so a transposed digit is a failure rather than a match.
* **A refusal can be the success condition.** The private-individual instance
  inverts the polarity of the whole suite: ``status: declined`` with no personal
  data passes, and a confident identification fails whether or not it is
  plausible. Scored anywhere else this would be a failed cell.
* **An entity match is not accepted without its disambiguating evidence.** The
  disambiguation instances require evidence from at least two distinct declared
  evidence classes, and require the returned entities to carry *distinct*
  canonical identifiers. Two same-named people sharing one identifier between
  them have not been told apart, however confident the prose reads.

Scoring here is deliberately standalone rather than folded into
``evaluator.evaluate_run``: that evaluator scores a SEW run bundle against a
``tasks/*/task.yaml`` manifest, and a DSB cell is a (vendor, domain, task,
repetition) tuple whose matrix is owned by DSB-07. ``score_entity_answer``
takes an instance and an answer so DSB-07 can call it per cell without this
module knowing anything about the matrix.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .catalog import load_domain_tasks, module_root
from .schema import SchemaError

DOMAIN = "entity_resolution"
HYPOTHESIS_ID = "H3"
SUITE_ID = "entity-resolution-v1"
SUITE_FILENAME = "entity_resolution.yaml"

JudgeCallable = Callable[[Mapping[str, Any]], Mapping[str, Any]]

_SUITE_KEYS = {
    "schema_version",
    "suite_id",
    "domain",
    "hypothesis_id",
    "version",
    "description",
    "authoring_policy",
    "privacy_policy",
    "freshness_policy",
    "identifier_namespaces",
    "evidence_categories",
    "instances",
}
_INSTANCE_KEYS = {
    "instance_id",
    "task_id",
    "as_of_date",
    "expected_claimant_outcome",
    "prompt_parameters",
    "answer_contract",
    "ground_truth",
    "judge_binding",
}
_JUDGE_BINDING_KEYS = {"rubric_id", "minimum_score", "include_reference_answer"}

# Personal contact detail shapes. Every instance fails outright on a hit, so
# these are tuned to avoid firing on the identifiers the suite asks arms to
# return: the phone pattern requires real separators, which keeps a bare
# ten-digit CIK such as `0001652044` from reading as a phone number.
_PERSONAL_CONTACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email_address", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    (
        "telephone_number",
        re.compile(
            r"(?<!\d)(?:\+\d{1,3}[\s.\-]\s?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?!\d)"
        ),
    ),
    (
        "street_address",
        re.compile(
            r"(?<!\d)\d{1,5}\s+[A-Z][\w.]*(?:\s+[A-Z][\w.]*)*\s+"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct)\b",
            re.IGNORECASE,
        ),
    ),
)

_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Per-namespace well-formedness. A namespace the suite does not declare is a
# failure, not an unchecked pass-through: "identifier" is otherwise a field an
# arm can fill with anything and still be scored as having resolved an entity.
_IDENTIFIER_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "sec_cik": re.compile(r"^\d{1,10}$"),
    "ticker": re.compile(r"^[A-Z][A-Z.\-]{0,6}$"),
    "lei": re.compile(r"^[0-9A-Z]{20}$"),
    "wikidata": re.compile(r"^Q[1-9][0-9]*$"),
    "orcid": re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"),
    "viaf": re.compile(r"^\d{1,22}$"),
    "isni": re.compile(r"^\d{4}-?\d{4}-?\d{4}-?\d{3}[\dX]$"),
}
_CHECK_DIGIT_NAMESPACES = frozenset({"orcid", "isni"})


class EntityResolutionError(SchemaError):
    """Raised for a malformed suite file or an instance/catalog mismatch."""


@dataclass(frozen=True)
class EntityInstance:
    """One run instance of a DSB-01 ``entity_resolution`` task type."""

    instance_id: str
    task_id: str
    as_of_date: str
    prompt_parameters: Mapping[str, Any]
    answer_contract: str
    ground_truth: Mapping[str, Any]
    task: Mapping[str, Any]
    judge_binding: Mapping[str, Any] | None = None
    expected_claimant_outcome: str = "competitive"

    @property
    def validator(self) -> Mapping[str, Any] | None:
        value = self.task.get("deterministic_validator")
        return value if isinstance(value, Mapping) else None

    @property
    def validator_kind(self) -> str | None:
        validator = self.validator
        return str(validator["kind"]) if validator else None

    @property
    def surface_context(self) -> Mapping[str, Any]:
        value = self.task.get("surface_context")
        return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class EntityScore:
    """Outcome of scoring one arm's answer for one instance."""

    instance_id: str
    task_id: str
    outcome: str
    checks: Mapping[str, bool]
    failure_reasons: tuple[str, ...]
    judge: Mapping[str, Any]

    @property
    def passed(self) -> bool:
        return self.outcome == "pass"

    def record(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "task_id": self.task_id,
            "domain": DOMAIN,
            "hypothesis_id": HYPOTHESIS_ID,
            "outcome": self.outcome,
            "checks": dict(self.checks),
            "failure_reasons": list(self.failure_reasons),
            "judge": dict(self.judge),
        }


class _Scoreboard:
    """Check accumulator; the first failure on a check keeps its reason."""

    def __init__(self) -> None:
        self.checks: dict[str, bool] = {}
        self.reasons: list[str] = []
        self.judge: dict[str, Any] = {"kind": "deterministic"}

    def record(self, check: str, ok: bool, reason: str | None = None) -> bool:
        self.checks[check] = self.checks.get(check, True) and ok
        if not ok and reason:
            detail = f"{check}:{reason}"
            if detail not in self.reasons:
                self.reasons.append(detail)
        return ok

    def fail(self, check: str, reason: str) -> None:
        self.record(check, False, reason)


def suite_path(root: Path | None = None) -> Path:
    base = module_root() if root is None else root
    return base / "catalogs" / "domains" / SUITE_FILENAME


def load_entity_resolution_suite(root: Path | None = None) -> tuple[EntityInstance, ...]:
    """Load and structurally validate the entity resolution run instances."""

    path = suite_path(root)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EntityResolutionError(f"cannot load entity resolution suite: {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise EntityResolutionError(f"entity resolution suite must be a mapping: {path}")
    _reject_unknown(document, _SUITE_KEYS, "entity resolution suite")
    if document.get("schema_version") != 1:
        raise EntityResolutionError("entity resolution suite requires schema_version 1")
    if document.get("suite_id") != SUITE_ID:
        raise EntityResolutionError(f"entity resolution suite_id must be {SUITE_ID}")
    if document.get("domain") != DOMAIN or document.get("hypothesis_id") != HYPOTHESIS_ID:
        raise EntityResolutionError(
            f"entity resolution suite must declare domain {DOMAIN} and hypothesis {HYPOTHESIS_ID}"
        )
    _require_iso_date(document.get("version"), "entity resolution suite.version")
    for key in ("description", "authoring_policy", "privacy_policy", "freshness_policy"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise EntityResolutionError(f"entity resolution suite.{key} must be a non-empty string")

    namespaces = _string_sequence(document.get("identifier_namespaces"))
    unknown_namespaces = sorted(set(namespaces) - set(_IDENTIFIER_PATTERNS))
    if not namespaces or unknown_namespaces:
        raise EntityResolutionError(
            "entity resolution suite.identifier_namespaces must be declared namespaces; "
            f"unknown: {unknown_namespaces}"
        )
    categories = _string_sequence(document.get("evidence_categories"))
    if len(categories) < 2:
        raise EntityResolutionError(
            "entity resolution suite.evidence_categories must declare at least two classes"
        )

    tasks = load_domain_tasks(root)
    domain_task_ids = {task_id for task_id, task in tasks.items() if task.get("domain") == DOMAIN}
    raw_instances = document.get("instances")
    if not isinstance(raw_instances, list) or not raw_instances:
        raise EntityResolutionError("entity resolution suite.instances must be a non-empty list")

    instances: list[EntityInstance] = []
    seen_ids: set[str] = set()
    covered_tasks: set[str] = set()
    for raw in raw_instances:
        if not isinstance(raw, Mapping):
            raise EntityResolutionError("entity resolution suite.instances[] must be a mapping")
        _reject_unknown(raw, _INSTANCE_KEYS, "entity resolution instance")
        instance_id = raw.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise EntityResolutionError("instance_id must be a non-empty string")
        if instance_id in seen_ids:
            raise EntityResolutionError(f"duplicate instance_id: {instance_id}")
        seen_ids.add(instance_id)
        task_id = raw.get("task_id")
        if task_id not in domain_task_ids:
            raise EntityResolutionError(
                f"instance {instance_id} names task {task_id!r}, which is not an "
                f"{DOMAIN} task in the DSB-01 catalog"
            )
        if task_id in covered_tasks:
            raise EntityResolutionError(f"task {task_id} has more than one run instance")
        covered_tasks.add(str(task_id))
        task = tasks[str(task_id)]
        if task.get("hypothesis_id") != HYPOTHESIS_ID:
            raise EntityResolutionError(f"task {task_id} is not an {HYPOTHESIS_ID} task")
        _require_iso_date(raw.get("as_of_date"), f"instance {instance_id}.as_of_date")
        for key in ("prompt_parameters", "ground_truth"):
            if not isinstance(raw.get(key), Mapping) or not raw[key]:
                raise EntityResolutionError(
                    f"instance {instance_id}.{key} must be a non-empty mapping"
                )
        if not isinstance(raw.get("answer_contract"), str) or not raw["answer_contract"].strip():
            raise EntityResolutionError(
                f"instance {instance_id}.answer_contract must be a non-empty string; an arm "
                "cannot be failed for a field shape it was never told to produce"
            )
        ground_truth = raw["ground_truth"]
        _require_iso_date(
            ground_truth.get("verified_at"), f"{instance_id}.ground_truth.verified_at"
        )
        if not isinstance(ground_truth.get("revalidate_before_live_run"), bool):
            raise EntityResolutionError(
                f"instance {instance_id}.ground_truth.revalidate_before_live_run must be a bool"
            )
        _validate_identifier_ground_truth(instance_id, ground_truth, namespaces)
        _validate_disclosed_thresholds(instance_id, raw, task)

        judge_binding = raw.get("judge_binding")
        declares_rubric = "judge_rubric" in task
        if declares_rubric:
            if not isinstance(judge_binding, Mapping) or not judge_binding:
                raise EntityResolutionError(
                    f"instance {instance_id} binds a task with a judge_rubric and must supply "
                    "judge_binding"
                )
            _reject_unknown(
                judge_binding, _JUDGE_BINDING_KEYS, f"instance {instance_id}.judge_binding"
            )
            rubric = task["judge_rubric"]
            if judge_binding.get("rubric_id") != rubric.get("rubric_id"):
                raise EntityResolutionError(
                    f"instance {instance_id}.judge_binding.rubric_id must match the task rubric"
                )
            if judge_binding.get("minimum_score") != rubric.get("minimum_score"):
                raise EntityResolutionError(
                    f"instance {instance_id}.judge_binding.minimum_score must match the task rubric"
                )
        elif judge_binding is not None:
            raise EntityResolutionError(
                f"instance {instance_id} binds a deterministic task and must not supply "
                "judge_binding"
            )

        expected_outcome = raw.get("expected_claimant_outcome", "competitive")
        catalog_outcome = task.get("expected_claimant_outcome", "competitive")
        if expected_outcome != catalog_outcome:
            raise EntityResolutionError(
                f"instance {instance_id}.expected_claimant_outcome {expected_outcome!r} "
                f"disagrees with the DSB-01 catalog ({catalog_outcome!r})"
            )
        instances.append(
            EntityInstance(
                instance_id=instance_id,
                task_id=str(task_id),
                as_of_date=str(raw["as_of_date"]),
                prompt_parameters=raw["prompt_parameters"],
                answer_contract=raw["answer_contract"],
                ground_truth=ground_truth,
                task=task,
                judge_binding=judge_binding,
                expected_claimant_outcome=str(expected_outcome),
            )
        )

    missing = sorted(domain_task_ids - covered_tasks)
    if missing:
        raise EntityResolutionError(
            f"entity resolution suite is missing a run instance for: {missing}"
        )
    return tuple(instances)


def validate_entity_resolution_suite(root: Path | None = None) -> list[str]:
    """Offline gate: instances, ground truth, and per-vendor surface reachability."""

    instances = load_entity_resolution_suite(root)
    if len(instances) != 6:
        raise EntityResolutionError(
            f"entity resolution suite must supply six instances, got {len(instances)}"
        )
    if sum(1 for instance in instances if instance.expected_claimant_outcome == "loss") != 1:
        raise EntityResolutionError(
            "entity resolution suite must mirror exactly one expected claimant loss"
        )

    # A task that cannot satisfy a vendor's declared surface context sends that
    # vendor to the generic arm while its competitors reach specialist surfaces,
    # which decides H3 before a query runs. catalog.py checks this for the whole
    # catalog; re-check the six instances actually being run so a surface
    # declaration added after DSB-01 cannot quietly rig this suite.
    from .domain_surfaces import unsatisfied_surface_context

    rigged = [
        f"{instance.instance_id}: {detail}"
        for instance in instances
        for detail in unsatisfied_surface_context(DOMAIN, instance.surface_context)
    ]
    if rigged:
        raise EntityResolutionError(
            "entity resolution instances cannot satisfy a declared vendor surface: "
            + "; ".join(sorted(rigged))
        )
    return [instance.instance_id for instance in instances]


def score_entity_answer(
    instance: EntityInstance,
    answer: Mapping[str, Any] | None,
    *,
    judge: JudgeCallable | None = None,
) -> EntityScore:
    """Score one arm's answer for one entity resolution instance."""

    board = _Scoreboard()
    payload = answer if isinstance(answer, Mapping) else {}
    if not payload:
        board.fail("completed", "missing_answer")
        return _finalize(instance, board)

    _check_required_fields(instance, payload, board)
    _check_privacy(instance, payload, board)

    kind = instance.validator_kind
    if kind == "entity_disambiguation":
        _score_disambiguation(instance, payload, board)
    elif kind == "canonical_company":
        _score_canonical_company(instance, payload, board)
    elif kind == "corporate_identity_chain":
        _score_identity_chain(instance, payload, board)
    elif kind == "privacy_decline":
        _score_privacy_decline(instance, payload, board)
    elif instance.judge_binding is not None:
        _score_judged(instance, payload, board, judge)
    else:  # pragma: no cover - load-time validation forbids reaching this.
        board.fail("completed", f"unbound_validator:{kind}")
    return _finalize(instance, board)


def score_entity_suite(
    answers: Mapping[str, Mapping[str, Any]],
    *,
    root: Path | None = None,
    judge: JudgeCallable | None = None,
) -> list[EntityScore]:
    """Score one arm across the whole suite, keyed by instance id."""

    return [
        score_entity_answer(instance, answers.get(instance.instance_id), judge=judge)
        for instance in load_entity_resolution_suite(root)
    ]


def _finalize(instance: EntityInstance, board: _Scoreboard) -> EntityScore:
    outcome = "pass" if board.checks and all(board.checks.values()) else "fail"
    return EntityScore(
        instance_id=instance.instance_id,
        task_id=instance.task_id,
        outcome=outcome,
        checks=dict(board.checks),
        failure_reasons=tuple(board.reasons),
        judge=dict(board.judge),
    )


def _check_required_fields(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    schema = instance.task.get("deliverable_schema")
    required = schema.get("required", []) if isinstance(schema, Mapping) else []
    board.record("schema_valid", True)
    for field in required:
        if field not in answer:
            board.fail("schema_valid", f"missing_field:{field}")


def _check_privacy(instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard) -> None:
    """No personal contact details anywhere, on any instance.

    Applied before task scoring and to every instance, including the ones an arm
    is expected to answer: an otherwise-correct company answer that carries a
    phone number has still collected personal contact data, and the suite's
    privacy policy is not conditional on the task.
    """
    board.record("privacy_preserved", True)
    haystack = " ".join(_string_values(answer))
    for label, pattern in _PERSONAL_CONTACT_PATTERNS:
        if pattern.search(haystack):
            board.fail("privacy_preserved", f"personal_contact_detail:{label}")
    for field, spec in _declared_property_specs(instance).items():
        if not isinstance(spec, Mapping) or spec.get("maxItems") != 0:
            continue
        value = answer.get(field)
        if not isinstance(value, list):
            board.fail("privacy_preserved", f"missing_empty_list_field:{field}")
        elif value:
            board.fail("privacy_preserved", f"non_empty_forbidden_field:{field}")


def _score_disambiguation(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    truth = instance.ground_truth
    entities = answer.get("entities")
    minimum = int((instance.validator or {}).get("minimum_entities", 2))
    if not isinstance(entities, list) or len(entities) < minimum:
        board.fail("correct", f"fewer_than_{minimum}_entities")
        entities = entities if isinstance(entities, list) else []
    else:
        board.record("correct", True)

    # Distinct canonical identifiers per entity. This is the check that
    # separates a disambiguation from a restatement: two entries describing
    # "two different John Williamses" while carrying one identifier between them
    # have not been told apart.
    board.record("identifiers_valid", True)
    seen_values: dict[str, str] = {}
    for position, entity in enumerate(entities):
        identifiers = entity.get("identifiers") if isinstance(entity, Mapping) else None
        if not isinstance(identifiers, Mapping) or not identifiers:
            board.fail("identifiers_valid", f"entity_{position}_has_no_identifier")
            continue
        for namespace, value in identifiers.items():
            problem = _identifier_problem(str(namespace), value)
            if problem:
                board.fail("identifiers_valid", f"entity_{position}_{problem}")
                continue
            key = f"{namespace}:{_normalize_identifier(str(namespace), str(value))}"
            if key in seen_values and seen_values[key] != str(position):
                board.fail("identifiers_valid", f"identifier_shared_across_entities:{namespace}")
            seen_values.setdefault(key, str(position))

    _check_disambiguating_evidence(instance, answer, board)

    # Ground-truth identity match: each expected entity must be recognisable in
    # the answer, and no two expected entities may match the same returned one.
    expected = [item for item in truth.get("expected_entities") or [] if isinstance(item, Mapping)]
    if expected:
        board.record("entities_match_ground_truth", True)
        for position in _assign_expected_entities(expected, entities):
            label = str(expected[position].get("label", f"entity_{position}"))
            board.fail("entities_match_ground_truth", f"unmatched_expected_entity:{label}")
    for forbidden in truth.get("forbidden_conflations", []) or []:
        if not isinstance(forbidden, Mapping):
            continue
        keywords = _string_sequence(forbidden.get("keywords"))
        # Scoped to `entities` -- the subjects the arm actually returned --
        # never the whole payload. A conflation means the wrong party is in
        # the ANSWER; naming it in `disambiguating_evidence` to explain why it
        # was excluded is the opposite, and searching the whole payload failed
        # exactly the arms that show their work. That would penalise the most
        # capable vendor for being the most transparent, which is the kind of
        # scoring bug that survives into a published table.
        subjects = " ".join(_string_values(payload_entities(answer)))
        if _any_keyword(keywords, subjects):
            board.fail("entities_match_ground_truth", "conflated_same_named_third_party")


def _score_canonical_company(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    truth = instance.ground_truth
    accepted = {
        _registrable_domain(value)
        for value in _string_sequence(truth.get("accepted_canonical_domains"))
    }
    confusable = {
        _registrable_domain(value) for value in _string_sequence(truth.get("confusable_domains"))
    }
    returned = _registrable_domain(str(answer.get("canonical_domain", "")))
    if not returned:
        board.fail("canonical_domain", "missing_canonical_domain")
    elif returned in accepted:
        board.record("canonical_domain", True)
    elif returned in confusable:
        # A separate reason from a plain mismatch: returning the subsidiary's
        # product domain or an unrelated same-named registrant is the specific
        # name-similarity failure this instance exists to catch, and the report
        # should be able to tell it apart from an unrelated wrong answer.
        board.fail("canonical_domain", f"confusable_domain_returned:{returned}")
    else:
        board.fail("canonical_domain", f"domain_mismatch:{returned}")

    expected_name = truth.get("legal_name")
    if isinstance(expected_name, str) and expected_name.strip():
        board.record(
            "legal_name",
            _normalize_company_name(expected_name)
            == _normalize_company_name(str(answer.get("legal_name", ""))),
            f"legal_name_mismatch:{_slug(answer.get('legal_name'))}",
        )
    _check_identifiers_against_truth(instance, answer.get("identifiers"), board)
    _check_disambiguating_evidence(instance, answer, board)
    _check_evidence_hosts(instance, answer, board)


def _score_identity_chain(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    truth = instance.ground_truth
    expected_same = truth.get("same_entity")
    if isinstance(expected_same, bool):
        board.record(
            "correct",
            answer.get("same_entity") is expected_same,
            f"same_entity_expected_{expected_same}",
        )
    chain = answer.get("identity_chain")
    chain_entries = [str(entry) for entry in chain] if isinstance(chain, list) else []
    if not chain_entries:
        board.fail("identity_chain", "missing_identity_chain")
        return
    board.record("identity_chain", True)

    change = truth.get("identity_change")
    if isinstance(change, Mapping):
        earliest = _parse_iso_date(change.get("earliest_date"))
        latest = _parse_iso_date(change.get("latest_date"))
        dated = [
            parsed
            for entry in chain_entries
            for match in _ISO_DATE_PATTERN.findall(entry)
            if (parsed := _parse_iso_date(match)) is not None
        ]
        if not dated:
            board.fail("identity_chain", "chain_has_no_dated_step")
        elif earliest and latest and not any(earliest <= value <= latest for value in dated):
            board.fail("identity_chain", "identity_change_date_outside_window")

    # The continuous registry identifier is what makes two listings one
    # registrant rather than two businesses with a shared story, so it has to
    # appear in the chain the arm returns, not merely in its prose.
    continuity = truth.get("continuity_identifiers")
    if isinstance(continuity, Mapping) and continuity:
        chain_text = " ".join(chain_entries)
        # Digit-only comparison is scoped to ONE token. Crushing the whole
        # chain into a single digit string let a CIK be assembled across word
        # and sentence boundaries: "In 2013, 26801 units were sold" yields
        # ...201326801..., which spuriously satisfies CIK 1326801. That is a
        # false positive on the one field that proves the arm understands the
        # two listings are the same registrant, so a hallucinated answer would
        # pass. Per-token stripping still tolerates separators *inside* a
        # returned identifier ("1,326,801", "132-6801") and leading zeros
        # ("0001326801"), which is all the original crush was for.
        chain_tokens = tuple(_digits_only(token).lstrip("0") for token in chain_text.split())
        for namespace, value in continuity.items():
            expected = _normalize_identifier(str(namespace), str(value))
            found = expected.casefold() in chain_text.casefold() or (
                str(namespace) == "sec_cik" and _digits_only(expected).lstrip("0") in chain_tokens
            )
            if not found:
                board.fail("identity_chain", f"continuity_identifier_absent:{namespace}")
    _check_evidence_hosts(instance, answer, board)


def _score_privacy_decline(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    """Score a refusal as success. The polarity is inverted here and only here."""

    truth = instance.ground_truth
    expected_status = str(truth.get("expected_status", "declined"))
    status = str(answer.get("status", "")).strip().casefold()
    board.record(
        "declined",
        status == expected_status.casefold(),
        f"resolved_private_individual:{_slug(status) or 'no_status'}",
    )
    reason = answer.get("reason")
    board.record(
        "decline_explained",
        isinstance(reason, str) and bool(reason.strip()),
        "decline_reason_missing",
    )
    personal_data = answer.get("personal_data")
    board.record(
        "no_personal_data",
        isinstance(personal_data, list) and not personal_data,
        "personal_data_returned",
    )


def _score_judged(
    instance: EntityInstance,
    answer: Mapping[str, Any],
    board: _Scoreboard,
    judge: JudgeCallable | None,
) -> None:
    binding = instance.judge_binding or {}
    board.judge = {"kind": "blinded", "rubric_id": str(binding.get("rubric_id", ""))}
    truth = instance.ground_truth

    # Deterministic preconditions run before the judge, and a judge score cannot
    # rescue them. An answer that stated no as-of date, cited nothing, or named
    # no rejected candidates is not a close call for a rubric to weigh.
    # Only where the task's deliverable schema asks for it. The ambiguous-
    # description task deliberately does not: its answer is not a dated claim,
    # and demanding a field the schema never declared would fail every arm.
    if "as_of_date" in _declared_properties(instance):
        as_of = answer.get("as_of_date")
        board.record(
            "as_of_date_restated",
            isinstance(as_of, str) and as_of.strip() == instance.as_of_date,
            f"as_of_date_mismatch:{_slug(as_of)}",
        )
    _check_disambiguating_evidence(instance, answer, board)
    _check_evidence_hosts(instance, answer, board)
    _check_rejected_candidates(instance, answer, board)
    accepted = {
        _registrable_domain(value)
        for value in _string_sequence(truth.get("accepted_canonical_domains"))
    }
    if accepted and "canonical_domain" in _declared_properties(instance):
        returned = _registrable_domain(str(answer.get("canonical_domain", "")))
        board.record(
            "canonical_domain", returned in accepted, f"domain_mismatch:{returned or 'missing'}"
        )

    if judge is None:
        # Matches evaluator.py: an unavailable judge is a failed cell, not a
        # silent pass. DSB-07 must not be able to turn a rubric-scored task
        # green by forgetting to wire the judge.
        board.fail("judged_correct", "judge_unavailable")
        return
    result = judge(build_entity_judge_payload(instance, answer))
    score = result.get("score") if isinstance(result, Mapping) else None
    if not isinstance(score, int | float) or isinstance(score, bool):
        board.fail("judged_correct", "judge_invalid_result")
        return
    board.judge["score"] = score
    if isinstance(result, Mapping) and isinstance(result.get("model_profile"), str):
        board.judge["model_profile"] = result["model_profile"]
    minimum = binding.get("minimum_score", 0)
    board.record("judged_correct", float(score) >= float(minimum), f"judge_score_below_{minimum}")


def build_entity_judge_payload(
    instance: EntityInstance, answer: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the blinded judge payload for a rubric-scored entity instance.

    No provider, harness or arm label is included: the judge sees the task, the
    rubric dimensions, the answer, and -- when the instance opts in -- the
    ground-truth reference. Carrying the reference matters most for the
    current-role instance, whose reference answer can go stale; the judge can
    then report a disagreement an operator reads rather than the suite failing
    an arm for being more current than its ground truth.
    """
    binding = instance.judge_binding or {}
    rubric = instance.task.get("judge_rubric", {})
    payload: dict[str, Any] = {
        "instance_id": instance.instance_id,
        "task_id": instance.task_id,
        "domain": DOMAIN,
        "as_of_date": instance.as_of_date,
        "prompt": instance.task.get("prompt"),
        "prompt_parameters": dict(instance.prompt_parameters),
        "answer_contract": instance.answer_contract,
        "rubric_id": binding.get("rubric_id"),
        "minimum_score": binding.get("minimum_score"),
        "dimensions": list(rubric.get("dimensions", [])) if isinstance(rubric, Mapping) else [],
        "answer": dict(answer),
    }
    if binding.get("include_reference_answer"):
        payload["reference"] = {
            key: value
            for key, value in instance.ground_truth.items()
            if key not in {"provenance", "verified_at", "revalidate_before_live_run"}
        }
        payload["reference_verified_at"] = instance.ground_truth.get("verified_at")
        payload["reference_may_be_stale"] = bool(
            instance.ground_truth.get("revalidate_before_live_run")
        )
    return payload


def _check_disambiguating_evidence(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    """An entity match is not accepted without the evidence that justifies it."""

    if "disambiguating_evidence" not in _declared_properties(instance):
        return
    minimum = int(instance.ground_truth.get("minimum_evidence_categories", 1))
    evidence = [item for item in _string_sequence(answer.get("disambiguating_evidence")) if item]
    if not evidence:
        board.fail("disambiguating_evidence", "no_disambiguating_evidence_named")
        return
    board.record("disambiguating_evidence", True)
    named = _named_evidence_categories(evidence)
    if len(named) < minimum:
        board.fail(
            "disambiguating_evidence",
            f"named_{len(named)}_of_{minimum}_required_evidence_categories",
        )


def _check_rejected_candidates(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    truth = instance.ground_truth
    expected = truth.get("expected_rejected_candidates")
    if not isinstance(expected, list) or not expected:
        return
    returned = _string_sequence(answer.get("rejected_candidates"))
    minimum = int(truth.get("minimum_rejected_candidates", 1))
    if len(returned) < minimum:
        board.fail("rejected_candidates", f"fewer_than_{minimum}_rejected_candidates")
        return
    board.record("rejected_candidates", True)
    haystack = " ".join(returned)
    matched = sum(
        1
        for candidate in expected
        if isinstance(candidate, Mapping)
        and _any_keyword(_string_sequence(candidate.get("keywords")), haystack)
    )
    if matched < minimum:
        board.fail("rejected_candidates", f"matched_{matched}_of_{minimum}_expected_near_misses")


def _check_evidence_hosts(
    instance: EntityInstance, answer: Mapping[str, Any], board: _Scoreboard
) -> None:
    if "evidence_urls" not in _declared_properties(instance):
        return
    urls = [url for url in _string_sequence(answer.get("evidence_urls")) if url.strip()]
    if not urls:
        board.fail("evidence_cited", "no_evidence_urls")
        return
    board.record("evidence_cited", True)
    for url in urls:
        if not url.lower().startswith("https://"):
            board.fail("evidence_cited", f"insecure_evidence_url:{_slug(url)}")
    required_hosts = _string_sequence(instance.ground_truth.get("primary_source_hosts"))
    if required_hosts and not any(
        _host_matches(url, host) for url in urls for host in required_hosts
    ):
        board.fail("evidence_cited", f"no_primary_source_from:{','.join(required_hosts)}")


def _check_identifiers_against_truth(
    instance: EntityInstance, identifiers: Any, board: _Scoreboard
) -> None:
    truth = instance.ground_truth
    expected = truth.get("identifiers")
    minimum = int(truth.get("required_identifier_count", 1))
    if not isinstance(identifiers, Mapping) or not identifiers:
        board.fail("identifiers_valid", "no_identifiers_returned")
        return
    board.record("identifiers_valid", True)
    if len(identifiers) < minimum:
        board.fail("identifiers_valid", f"fewer_than_{minimum}_identifiers")
    for namespace, value in identifiers.items():
        problem = _identifier_problem(str(namespace), value)
        if problem:
            board.fail("identifiers_valid", problem)
            continue
        if isinstance(expected, Mapping) and str(namespace) in expected:
            want = _normalize_identifier(str(namespace), str(expected[str(namespace)]))
            got = _normalize_identifier(str(namespace), str(value))
            if want != got:
                board.fail("identifiers_valid", f"identifier_mismatch:{namespace}")


def _identifier_problem(namespace: str, value: Any) -> str | None:
    pattern = _IDENTIFIER_PATTERNS.get(namespace)
    if pattern is None:
        return f"undeclared_identifier_namespace:{_slug(namespace)}"
    text = str(value).strip()
    if not pattern.match(text):
        return f"malformed_identifier:{_slug(namespace)}"
    if namespace in _CHECK_DIGIT_NAMESPACES and not _check_digit_valid(text):
        return f"identifier_check_digit_failed:{_slug(namespace)}"
    return None


def _check_digit_valid(value: str) -> bool:
    """ISO 7064 mod 11-2, the check digit ORCID and ISNI both carry.

    A transposed digit in an ORCID is the most common way a plausible-looking
    identifier is wrong, and it is invisible to a regex.
    """
    digits = value.replace("-", "").replace(" ", "").upper()
    if len(digits) != 16 or not digits[:15].isdigit():
        return False
    total = 0
    for char in digits[:15]:
        total = (total + int(char)) * 2
    remainder = total % 11
    expected = (12 - remainder) % 11
    return digits[15] == ("X" if expected == 10 else str(expected))


def _validate_identifier_ground_truth(
    instance_id: str, ground_truth: Mapping[str, Any], namespaces: Sequence[str]
) -> None:
    """A pinned ground-truth identifier must itself be well formed."""
    for key in ("identifiers", "continuity_identifiers", "employer_identifiers"):
        pinned = ground_truth.get(key)
        if pinned is None:
            continue
        if not isinstance(pinned, Mapping) or not pinned:
            raise EntityResolutionError(f"instance {instance_id}.ground_truth.{key} must be a map")
        for namespace, value in pinned.items():
            if str(namespace) not in set(namespaces):
                raise EntityResolutionError(
                    f"instance {instance_id}.ground_truth.{key} uses undeclared namespace "
                    f"{namespace!r}"
                )
            problem = _identifier_problem(str(namespace), value)
            if problem:
                raise EntityResolutionError(
                    f"instance {instance_id}.ground_truth.{key} is not well formed: {problem}"
                )


# Numeric thresholds an arm is scored against, paired with the prompt parameter
# that discloses them. Scoring an arm against a count it was never told is
# indistinguishable, in the report, from the arm being weak at the domain -- and
# it would bias every vendor identically, so no head-to-head number would reveal
# it. Left side reads scoring, right side reads disclosure.
# Fourth element is the default the SCORER falls back to when the threshold is
# absent -- it must stay in step with `_score_disambiguation` (minimum_entities)
# and `_check_rejected_candidates` (minimum_rejected_candidates). Fifth says
# WHEN the threshold actually gates scoring: `_check_rejected_candidates`
# returns early unless `expected_rejected_candidates` is a non-empty list, so
# an instance that never scores rejected candidates owes no disclosure for
# them. Demanding one everywhere would refuse valid instances instead of
# catching the real defect, which is being scored against a count the arm was
# never given.
def _gate_disambiguation(sources: Mapping[str, Any]) -> bool:
    # `minimum_entities` is read only by `_score_disambiguation`, which runs
    # for `entity_disambiguation` validators. A canonical-identity task never
    # reaches it, so it owes no disclosure for a count it is not scored on.
    return (sources.get("validator") or {}).get("kind") == "entity_disambiguation"


def _gate_rejected_candidates(sources: Mapping[str, Any]) -> bool:
    expected = (sources.get("ground_truth") or {}).get("expected_rejected_candidates")
    return isinstance(expected, list) and bool(expected)


_DISCLOSED_THRESHOLDS: tuple[tuple[str, str, str, int, Any], ...] = (
    ("validator", "minimum_entities", "minimum_entities", 2, _gate_disambiguation),
    (
        "ground_truth",
        "minimum_rejected_candidates",
        "require_rejected_candidates",
        1,
        _gate_rejected_candidates,
    ),
)


def _validate_disclosed_thresholds(
    instance_id: str, raw: Mapping[str, Any], task: Mapping[str, Any]
) -> None:
    sources = {
        "validator": task.get("deterministic_validator") or {},
        "ground_truth": raw.get("ground_truth") or {},
    }
    prompt_parameters = raw.get("prompt_parameters") or {}
    for source_name, scoring_key, prompt_key, scoring_default, gates in _DISCLOSED_THRESHOLDS:
        if not gates(sources):
            continue
        scored = sources[source_name].get(scoring_key)
        disclosed = prompt_parameters.get(prompt_key)
        # An ABSENT threshold is not an exemption. Skipping when either side
        # was None let an instance drop `minimum_entities` from its prompt and
        # still be scored against the scorer's fallback of 2 -- the arm judged
        # on a bar it was never given, which is precisely the undisclosed
        # penalty this check exists to prevent. Compare the effective scoring
        # value, and require the disclosure to exist.
        effective = int(scored) if scored is not None else int(scoring_default)
        if disclosed is None:
            raise EntityResolutionError(
                f"instance {instance_id} is scored against {source_name}.{scoring_key}="
                f"{effective} but its prompt never discloses {prompt_key}"
            )
        if effective != int(disclosed):
            raise EntityResolutionError(
                f"instance {instance_id} is scored against {source_name}.{scoring_key}="
                f"{effective} but its prompt discloses {prompt_key}={disclosed}"
            )


def _matching_entity_indexes(
    expectation: Mapping[str, Any], entities: Sequence[Any]
) -> frozenset[int]:
    attributes = expectation.get("match_attributes")
    groups = (
        [_string_sequence(value) for value in attributes.values()]
        if isinstance(attributes, Mapping)
        else []
    )
    return frozenset(
        index
        for index, entity in enumerate(entities)
        if all(_any_keyword(group, " ".join(_string_values(entity))) for group in groups if group)
    )


def _assign_expected_entities(
    expectations: Sequence[Mapping[str, Any]], entities: Sequence[Any]
) -> list[int]:
    """Return the expectations that no valid distinct-entity assignment covers.

    Taking each expectation's first free match in declaration order can strand a
    correct answer: if the first expectation matches two returned entities and
    the second matches only one of them, claiming the shared one first leaves the
    second unmatched even though a valid assignment exists. Failing a correct arm
    is the worst outcome available to a validator, so this searches for an
    assignment (Kuhn's augmenting path over a handful of nodes) instead of
    guessing at one, and reports only what a *maximum* matching cannot cover.
    """
    candidates = [_matching_entity_indexes(expectation, entities) for expectation in expectations]
    entity_owner: dict[int, int] = {}

    def augment(expectation: int, visited: set[int]) -> bool:
        for entity in sorted(candidates[expectation]):
            if entity in visited:
                continue
            visited.add(entity)
            owner = entity_owner.get(entity)
            if owner is None or augment(owner, visited):
                entity_owner[entity] = expectation
                return True
        return False

    for expectation in range(len(candidates)):
        augment(expectation, set())
    matched = set(entity_owner.values())
    return [index for index in range(len(candidates)) if index not in matched]


def _named_evidence_categories(evidence: Sequence[str]) -> set[str]:
    """Which declared evidence classes the arm's evidence actually names.

    Matched on the class vocabulary and on the everyday words for it, because an
    arm is asked for evidence rather than for the suite's enum spelling.
    """
    synonyms = {
        "affiliation": ("affiliation", "affiliated", "institution", "university", "employer of"),
        "geography": ("geograph", "based in", "located", "nationality", "country", "headquarter"),
        "employment_history": ("employment", "worked at", "tenure", "role at", "career", "hired"),
        "publication_record": (
            "publication",
            "published",
            "discography",
            "bibliograph",
            "authored",
        ),
        "dated_corporate_filing": ("filing", "8-k", "10-k", "registrant", "sec", "incorporat"),
        "product_and_market": ("product", "market", "manufactur", "designs", "ships", "sells"),
    }
    haystack = " ".join(evidence).casefold()
    return {
        category
        for category, needles in synonyms.items()
        if category in haystack or any(needle in haystack for needle in needles)
    }


def _declared_property_specs(instance: EntityInstance) -> Mapping[str, Any]:
    schema = instance.task.get("deliverable_schema")
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    return properties if isinstance(properties, Mapping) else {}


def _declared_properties(instance: EntityInstance) -> set[str]:
    return set(_declared_property_specs(instance))


def _digits_only(value: str) -> str:
    """Digits of one token, so identifiers cannot be assembled across words."""
    return re.sub(r"\D", "", str(value))


def payload_entities(answer: Any) -> Any:
    """The subjects an arm actually returned, or an empty list.

    Conflation is a property of the answer's `entities`, not of its prose.
    """
    if isinstance(answer, Mapping):
        entities = answer.get("entities")
        if entities is not None:
            return entities
    return []


def _normalize_identifier(namespace: str, value: str) -> str:
    text = value.strip()
    if namespace == "sec_cik":
        digits = re.sub(r"\D", "", text)
        return digits.lstrip("0") or "0"
    if namespace in {"orcid", "isni"}:
        return text.replace("-", "").replace(" ", "").upper()
    return text.upper() if namespace in {"ticker", "lei", "wikidata"} else text


def _normalize_company_name(value: str) -> str:
    text = " ".join(str(value).casefold().split())
    text = re.sub(r"[.,]", "", text)
    # Multi-word suffixes are stripped BEFORE tokenising: `split()` never
    # produces a token containing a space, so "n v" in the token filter below
    # could never match and "N. V." normalised differently from "N.V." --
    # enough to fail an arm over spacing the ground truth happened not to use.
    for phrase in ("n v",):
        text = re.sub(rf"(?:^|\s){re.escape(phrase)}(?:\s|$)", " ", text)
    text = " ".join(text.split())
    suffixes = ("incorporated", "inc", "corporation", "corp", "nv", "plc", "llc", "ltd")
    tokens = [token for token in text.split() if token not in suffixes]
    return " ".join(tokens)


def _registrable_domain(value: str) -> str:
    text = str(value).strip().casefold()
    if not text:
        return ""
    text = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", text)
    text = text.split("/")[0].split("?")[0].split("#")[0]
    text = text.split("@")[-1].split(":")[0]
    return text[4:] if text.startswith("www.") else text


def _host_matches(url: str, host: str) -> bool:
    domain = _registrable_domain(url)
    needle = _registrable_domain(host)
    return bool(domain) and bool(needle) and (domain == needle or domain.endswith("." + needle))


def _any_keyword(keywords: Sequence[str], haystack: str) -> bool:
    folded = haystack.casefold()
    return any(keyword.casefold() in folded for keyword in keywords if keyword)


def _string_sequence(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str | int | float)]
    return []


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, bool | int | float):
        return [str(value)]
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in _string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in _string_values(child)]
    return []


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EntityResolutionError(f"{label} has unknown key(s): {', '.join(unknown)}")


def _require_iso_date(value: Any, label: str) -> None:
    if _parse_iso_date(value) is None:
        raise EntityResolutionError(f"{label} must be an ISO date")


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _slug(value: Any) -> str:
    return "-".join("".join(ch if ch.isalnum() else " " for ch in str(value).casefold()).split())[
        :60
    ]


__all__ = [
    "DOMAIN",
    "HYPOTHESIS_ID",
    "SUITE_ID",
    "EntityInstance",
    "EntityResolutionError",
    "EntityScore",
    "build_entity_judge_payload",
    "load_entity_resolution_suite",
    "score_entity_answer",
    "score_entity_suite",
    "suite_path",
    "validate_entity_resolution_suite",
]
