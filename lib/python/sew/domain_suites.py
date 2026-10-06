"""Runnable DSB domain suites: task instances, ground truth, and surface records.

The DSB-01 catalog (``catalogs/domains/tasks.yaml``) declares task *types* and
the evaluator contract each one carries. It deliberately stops short of ground
truth -- its own ``execution_contract`` says the domain suites supply "per-run
instance parameters, ground truth records, and executable validator or judge
bindings". This module is that layer for one domain at a time.

A suite manifest lives at ``catalogs/domains/suites/<domain>.yaml`` and binds
each task type to one public repository plus the references a correct answer
must cite. Two properties are load-bearing and are enforced here rather than
left to reviewer diligence:

* **Ground truth is resolvable, not asserted.** Every reference carries the
  repository and the number (or tag, or path) it must resolve to, and the
  declared ``url`` is re-parsed and checked against those fields at load time.
  A manifest whose URL and fields disagree fails offline, before anything is
  scored. ``verify_ground_truth`` re-resolves the same references against the
  live GitHub API on demand.
* **No instance may strand a vendor on the generic arm.** An instance can
  extend the task's ``surface_context`` -- the Pillow migration-guide instance
  has to, because its answer lives on a docs host that the catalog's code-host
  source policy does not include. The same seam points the other way: an
  instance that replaced a vendor's required context with an empty or narrowed
  value would demote that vendor to ``generic_surface`` while its competitors
  kept specialist arms, and the honest ``generic_surface`` marking would make
  the rigged run look correctly reported. Two rules close that: the merged
  context must still satisfy every declared surface, and an instance may widen
  a source policy but never narrow it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .catalog import load_domain_tasks, module_root
from .domain_surfaces import (
    PROVIDERS,
    DomainSurfaceError,
    resolve_domain_surface,
    unsatisfied_surface_context,
)
from .schema import SchemaError

SUITE_KEYS = frozenset(
    {
        "schema_version",
        "suite_id",
        "catalog_id",
        "domain",
        "hypothesis_id",
        "version",
        "description",
        "ground_truth_resolver",
        "ground_truth_resolved_at",
        "selection_frame",
        "instances",
    }
)
SELECTION_FRAME_KEYS = frozenset(
    {
        "policy",
        "procedure",
        "vendor_indexes_consulted",
        "distinct_organizations",
        "expected_loss_task_id",
    }
)
INSTANCE_KEYS = frozenset(
    {
        "instance_id",
        "task_id",
        "repository",
        "question",
        "provenance",
        "notes",
        "surface_context",
        "reference_answer",
        "expected_references",
        "expected_fields",
    }
)
REFERENCE_KEYS = frozenset(
    {
        "answer_key",
        "role",
        "kind",
        "repository",
        "number",
        "state",
        "tag",
        "ref",
        "commit",
        "path",
        "line_range",
        "comment_id",
        "url",
        "observed_at",
    }
)
FIELD_KEYS = frozenset({"answer_key", "match", "value", "values", "forbidden"})

REFERENCE_KINDS = frozenset({"pull", "issue", "issue_comment", "blob", "release"})
REFERENCE_ROLES = frozenset(
    {
        "pull_request",
        "issue",
        "comment",
        "implementation",
        "call_site",
        "release",
        "change",
        "migration_guide",
    }
)
REFERENCE_STATES = frozenset({"merged", "closed", "open"})
FIELD_MATCHES = frozenset({"exact", "includes"})


class SuiteError(SchemaError):
    """Raised for a malformed suite manifest or an unknown suite."""


@dataclass(frozen=True)
class Reference:
    """One ground-truth reference an answer key must resolve to."""

    answer_key: str
    role: str
    kind: str
    repository: str
    url: str
    number: int | None = None
    state: str | None = None
    tag: str | None = None
    ref: str | None = None
    commit: str | None = None
    path: str | None = None
    line_range: tuple[int, int] | None = None
    comment_id: int | None = None
    observed_at: str | None = None

    @property
    def pinned_ref(self) -> str | None:
        """The blob ref this reference was recorded at, commit taking priority."""
        return self.commit or self.ref


@dataclass(frozen=True)
class FieldExpectation:
    """A non-reference answer field the deterministic validator checks."""

    answer_key: str
    match: str
    value: str | None = None
    values: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


@dataclass(frozen=True)
class Instance:
    instance_id: str
    task_id: str
    domain: str
    repository: str
    question: str
    task: Mapping[str, Any]
    surface_context: Mapping[str, Any]
    references: tuple[Reference, ...]
    fields: tuple[FieldExpectation, ...]
    reference_answer: Mapping[str, Any] | None = None

    @property
    def validator(self) -> Mapping[str, Any] | None:
        value = self.task.get("deterministic_validator")
        return value if isinstance(value, Mapping) else None

    @property
    def rubric(self) -> Mapping[str, Any] | None:
        value = self.task.get("judge_rubric")
        return value if isinstance(value, Mapping) else None

    def references_for(self, answer_key: str) -> tuple[Reference, ...]:
        return tuple(ref for ref in self.references if ref.answer_key == answer_key)


@dataclass(frozen=True)
class DomainSuite:
    suite_id: str
    domain: str
    hypothesis_id: str
    version: str
    selection_frame: Mapping[str, Any]
    instances: tuple[Instance, ...]

    def instance(self, instance_id: str) -> Instance:
        for candidate in self.instances:
            if candidate.instance_id == instance_id:
                return candidate
        raise SuiteError(f"unknown instance: {instance_id}")


def suite_path(domain: str, root: Path | None = None) -> Path:
    base = module_root() if root is None else root
    return base / "catalogs" / "domains" / "suites" / f"{domain}.yaml"


def available_domains(root: Path | None = None) -> list[str]:
    base = module_root() if root is None else root
    directory = base / "catalogs" / "domains" / "suites"
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.yaml"))


def _is_positive_int(value: object) -> bool:
    """A positive-integer bound, rejecting YAML booleans.

    `bool` subclasses `int`, so a bare `isinstance(value, int)` accepts `true`
    and coerces it to `1`. That let `line_range: [true, 100]` load as
    `(1, 100)` and `number: true` load as pull #1 -- a malformed manifest
    becoming silently valid ground truth instead of a loader error.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuiteError(f"{label} must be a non-empty string")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: Iterable[str], label: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise SuiteError(f"{label} has unknown key(s): {', '.join(unknown)}")


def _iso_date(value: object, label: str) -> str:
    text = _require(value, label)
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise SuiteError(f"{label} must be an ISO date") from exc
    return text


def _repository(value: object, label: str) -> str:
    text = _require(value, label)
    parts = text.split("/")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise SuiteError(f"{label} must be owner/name, got {text!r}")
    return text


def _parse_reference(raw: Mapping[str, Any], label: str) -> Reference:
    if not isinstance(raw, Mapping):
        raise SuiteError(f"{label} must be a mapping")
    _reject_unknown(raw, REFERENCE_KEYS, label)
    kind = _require(raw.get("kind"), f"{label}.kind")
    if kind not in REFERENCE_KINDS:
        raise SuiteError(f"{label}.kind must be one of {sorted(REFERENCE_KINDS)}; got {kind!r}")
    role = _require(raw.get("role"), f"{label}.role")
    if role not in REFERENCE_ROLES:
        raise SuiteError(f"{label}.role must be one of {sorted(REFERENCE_ROLES)}; got {role!r}")
    state = raw.get("state")
    if state is not None and state not in REFERENCE_STATES:
        raise SuiteError(f"{label}.state must be one of {sorted(REFERENCE_STATES)}")
    line_range = raw.get("line_range")
    parsed_range: tuple[int, int] | None = None
    if line_range is not None:
        if (
            not isinstance(line_range, Sequence)
            or isinstance(line_range, (str, bytes))
            or len(line_range) != 2
            or not all(_is_positive_int(bound) for bound in line_range)
            or line_range[0] > line_range[1]
        ):
            raise SuiteError(f"{label}.line_range must be [start, end] with 0 < start <= end")
        parsed_range = (int(line_range[0]), int(line_range[1]))
    number = raw.get("number")
    if number is not None and not _is_positive_int(number):
        raise SuiteError(f"{label}.number must be a positive integer")
    comment_id = raw.get("comment_id")
    if comment_id is not None and not _is_positive_int(comment_id):
        raise SuiteError(f"{label}.comment_id must be a positive integer")
    reference = Reference(
        answer_key=_require(raw.get("answer_key"), f"{label}.answer_key"),
        role=role,
        kind=kind,
        repository=_repository(raw.get("repository"), f"{label}.repository"),
        url=_require(raw.get("url"), f"{label}.url"),
        number=number,
        state=state,
        tag=raw.get("tag"),
        ref=raw.get("ref"),
        commit=raw.get("commit"),
        path=raw.get("path"),
        line_range=parsed_range,
        comment_id=comment_id,
        observed_at=_iso_date(raw["observed_at"], f"{label}.observed_at")
        if "observed_at" in raw
        else None,
    )
    _require_kind_fields(reference, label)
    _assert_url_agrees(reference, label)
    return reference


def _require_kind_fields(reference: Reference, label: str) -> None:
    if reference.kind in {"pull", "issue"} and reference.number is None:
        raise SuiteError(f"{label} of kind {reference.kind} requires a number")
    if reference.kind == "issue" and reference.state == "merged":
        # Only a pull request can be merged. Live verification reads a merged
        # claim on an issue as "closed", so the nonsense claim would pass the
        # ground-truth gate on any closed issue; refuse it where it is written.
        raise SuiteError(
            f"{label} of kind issue cannot be state merged; only a pull request merges (use closed)"
        )
    if reference.kind == "issue_comment" and (
        reference.number is None or reference.comment_id is None
    ):
        raise SuiteError(f"{label} of kind issue_comment requires a number and a comment_id")
    if reference.kind == "release" and not reference.tag:
        raise SuiteError(f"{label} of kind release requires a tag")
    if reference.kind == "blob":
        if not reference.path:
            raise SuiteError(f"{label} of kind blob requires a path")
        if not reference.pinned_ref:
            raise SuiteError(f"{label} of kind blob requires a commit or ref")
    elif reference.line_range is not None:
        # `match_reference` only enforces line bounds for blob references, and
        # `parse_github_url` only reads line anchors off blob/tree URLs. A
        # `line_range` on a pull, issue, comment or release reference is
        # therefore a constraint the scorer can never apply -- declared
        # precision that reads as checked ground truth and is not.
        raise SuiteError(
            f"{label} of kind {reference.kind} may not declare a line_range; the runtime "
            "scorer only enforces line bounds for blob references"
        )


def _assert_url_agrees(reference: Reference, label: str) -> None:
    """Re-parse the declared URL and check it says the same thing as the fields.

    A manifest is the only thing standing between a reviewer and an unverifiable
    claim, so a reference whose URL and structured fields disagree -- the classic
    copy-paste of a neighbouring pull request number -- must fail at load time
    rather than silently score answers against whichever half the code happened
    to read.
    """
    from .domain_validators import parse_github_url

    parsed = parse_github_url(reference.url)
    if parsed is None:
        raise SuiteError(f"{label}.url is not a parseable github.com reference: {reference.url}")
    if parsed.repository.lower() != reference.repository.lower():
        raise SuiteError(
            f"{label}.url points at {parsed.repository}, but repository is {reference.repository}"
        )
    if reference.number is not None and parsed.number != reference.number:
        raise SuiteError(
            f"{label}.url points at #{parsed.number}, but number is {reference.number}"
        )
    if reference.comment_id is not None and parsed.comment_id != reference.comment_id:
        raise SuiteError(f"{label}.url comment anchor does not match comment_id")
    if reference.tag is not None and parsed.tag != reference.tag:
        raise SuiteError(f"{label}.url tag does not match tag {reference.tag!r}")
    if reference.path is not None and parsed.path != reference.path:
        raise SuiteError(f"{label}.url path does not match path {reference.path!r}")
    if reference.pinned_ref is not None and parsed.ref != reference.pinned_ref:
        raise SuiteError(f"{label}.url ref does not match {reference.pinned_ref!r}")
    if reference.line_range is not None:
        # The example URL's own anchor was never compared to the structured
        # bounds, so `line_range: [100, 110]` beside a URL ending `#L200`
        # loaded clean and the mismatch only surfaced by hand -- in a manifest
        # whose entire job is to be the checkable half of a claim.
        #
        # This check must mirror `match_reference` exactly, not merely overlap
        # with it. A loader looser than the scorer admits a manifest whose own
        # example URL is unscoreable: `match_reference` rejects a missing
        # anchor (`missing_line_anchor`) and an end anchor outside the bounds
        # (`line_span_too_broad`), so a manifest that skips either check ships
        # a reference answer that can never be graded correct.
        start, end = reference.line_range
        if parsed.line_start is None:
            raise SuiteError(
                f"{label}.url declares line_range {list(reference.line_range)} but carries no "
                "line anchor, which the runtime scorer rejects as missing_line_anchor"
            )
        if not (start <= parsed.line_start <= end):
            raise SuiteError(
                f"{label}.url line anchor L{parsed.line_start} is outside "
                f"line_range {list(reference.line_range)}"
            )
        if parsed.line_end is not None and not (start <= parsed.line_end <= end):
            raise SuiteError(
                f"{label}.url line anchor spans to L{parsed.line_end}, outside "
                f"line_range {list(reference.line_range)}, which the runtime scorer "
                "rejects as line_span_too_broad"
            )


def _parse_field(raw: Mapping[str, Any], label: str) -> FieldExpectation:
    if not isinstance(raw, Mapping):
        raise SuiteError(f"{label} must be a mapping")
    _reject_unknown(raw, FIELD_KEYS, label)
    match = _require(raw.get("match"), f"{label}.match")
    if match not in FIELD_MATCHES:
        raise SuiteError(f"{label}.match must be one of {sorted(FIELD_MATCHES)}")
    values = tuple(str(item) for item in raw.get("values", ()) or ())
    forbidden = tuple(str(item) for item in raw.get("forbidden", ()) or ())
    value = raw.get("value")
    if match == "exact" and not isinstance(value, str):
        raise SuiteError(f"{label}.value is required for an exact match")
    if match == "includes" and not values:
        raise SuiteError(f"{label}.values must be non-empty for an includes match")
    overlap = sorted(set(values) & set(forbidden))
    if overlap:
        raise SuiteError(f"{label} lists {overlap} as both required and forbidden")
    return FieldExpectation(
        answer_key=_require(raw.get("answer_key"), f"{label}.answer_key"),
        match=match,
        value=value if isinstance(value, str) else None,
        values=values,
        forbidden=forbidden,
    )


def _merge_surface_context(
    task: Mapping[str, Any], instance_context: Mapping[str, Any] | None, label: str
) -> dict[str, Any]:
    from .catalog import SURFACE_CONTEXT_KEYS

    task_context = dict(task.get("surface_context") or {})
    merged = dict(task_context)
    if instance_context is not None:
        if not isinstance(instance_context, Mapping):
            raise SuiteError(f"{label}.surface_context must be a mapping")
        _reject_unknown(instance_context, SURFACE_CONTEXT_KEYS, f"{label}.surface_context")
        merged.update(dict(instance_context))
    _assert_policy_only_widens(task_context, merged, label)
    return merged


def _assert_policy_only_widens(
    task_context: Mapping[str, Any], merged: Mapping[str, Any], label: str
) -> None:
    """An instance may widen a task's source policy, never narrow it.

    Widening is legitimate and sometimes required: the Pillow migration-guide
    instance has to reach a docs host that the code-host policy does not list.
    Narrowing is the same lever pointed the other way -- an instance could
    exclude the host its own answer lives on, and the arm bound by that policy
    would fail for a reason that has nothing to do with its index. The rule is
    asymmetric because the risks are.
    """
    task_policy = task_context.get("source_policy") or {}
    merged_policy = merged.get("source_policy") or {}
    if not isinstance(task_policy, Mapping) or not isinstance(merged_policy, Mapping):
        return
    task_domains = set(task_policy.get("include_domains") or ())
    merged_domains = set(merged_policy.get("include_domains") or ())
    dropped = sorted(task_domains - merged_domains)
    if dropped:
        raise SuiteError(
            f"{label}.surface_context narrows the task source policy by dropping {dropped}; "
            "an instance may widen a policy but never narrow it"
        )


def load_domain_suite(domain: str, root: Path | None = None) -> DomainSuite:
    """Load and strictly validate one domain's runnable instance manifest."""

    base = module_root() if root is None else root
    path = suite_path(domain, base)
    if not path.is_file():
        raise SuiteError(f"no suite manifest for domain {domain!r}: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SuiteError(f"cannot load domain suite: {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise SuiteError(f"domain suite must be a mapping: {path}")
    _reject_unknown(raw, SUITE_KEYS, "domain suite")
    if raw.get("schema_version") != 1:
        raise SuiteError("domain suite requires schema_version 1")
    if raw.get("catalog_id") != "domain-strengths-v1":
        raise SuiteError("domain suite.catalog_id must be domain-strengths-v1")
    if raw.get("domain") != domain:
        raise SuiteError(f"domain suite.domain must be {domain!r}")
    suite_id = _require(raw.get("suite_id"), "domain suite.suite_id")
    hypothesis_id = _require(raw.get("hypothesis_id"), "domain suite.hypothesis_id")
    version = _iso_date(raw.get("version"), "domain suite.version")
    _iso_date(raw.get("ground_truth_resolved_at"), "domain suite.ground_truth_resolved_at")

    selection_frame = raw.get("selection_frame")
    if not isinstance(selection_frame, Mapping) or not selection_frame:
        raise SuiteError("domain suite.selection_frame must be a non-empty mapping")
    _reject_unknown(selection_frame, SELECTION_FRAME_KEYS, "domain suite.selection_frame")
    _require(selection_frame.get("policy"), "domain suite.selection_frame.policy")
    if selection_frame.get("vendor_indexes_consulted") != "none":
        raise SuiteError(
            "domain suite.selection_frame.vendor_indexes_consulted must be 'none': selecting "
            "instances with a vendor index is the bias DSB exists to rule out"
        )

    from .domain_validators import ValidationError, assert_instance_contract

    tasks = load_domain_tasks(base)
    domain_task_ids = {task_id for task_id, task in tasks.items() if task.get("domain") == domain}
    if not domain_task_ids:
        raise SuiteError(f"catalog declares no tasks for domain {domain!r}")

    raw_instances = raw.get("instances")
    if not isinstance(raw_instances, list) or not raw_instances:
        raise SuiteError("domain suite.instances must be a non-empty list")

    instances: list[Instance] = []
    seen_ids: set[str] = set()
    covered: set[str] = set()
    repositories: set[str] = set()
    for entry in raw_instances:
        if not isinstance(entry, Mapping):
            raise SuiteError("domain suite.instances[] must be a mapping")
        _reject_unknown(entry, INSTANCE_KEYS, "domain suite.instances[]")
        instance_id = _require(entry.get("instance_id"), "instance_id")
        label = f"instance {instance_id}"
        if instance_id in seen_ids:
            raise SuiteError(f"duplicate instance_id: {instance_id}")
        seen_ids.add(instance_id)
        task_id = _require(entry.get("task_id"), f"{label}.task_id")
        if task_id not in domain_task_ids:
            raise SuiteError(f"{label} names task {task_id!r}, which is not a {domain} task")
        if task_id in covered:
            raise SuiteError(f"{label} duplicates task {task_id!r}; one instance per task type")
        covered.add(task_id)
        task = tasks[task_id]
        if task.get("hypothesis_id") != hypothesis_id:
            raise SuiteError(f"{label} task hypothesis does not match the suite hypothesis")
        repository = _repository(entry.get("repository"), f"{label}.repository")
        if repository.lower() in repositories:
            raise SuiteError(
                f"{label} reuses repository {repository!r}; one repository per instance keeps a "
                "single repository's index coverage from deciding more than one task"
            )
        repositories.add(repository.lower())
        question = _require(entry.get("question"), f"{label}.question")

        references = tuple(
            _parse_reference(item, f"{label}.expected_references[{index}]")
            for index, item in enumerate(entry.get("expected_references") or ())
        )
        if not references:
            raise SuiteError(f"{label} must declare at least one expected reference")
        fields = tuple(
            _parse_field(item, f"{label}.expected_fields[{index}]")
            for index, item in enumerate(entry.get("expected_fields") or ())
        )
        _assert_answer_keys_declared(task, references, fields, label)
        for reference in references:
            if reference.repository.lower() != repository.lower() and reference.role != "comment":
                raise SuiteError(
                    f"{label}.expected_references cites {reference.repository}, which is not the "
                    f"instance repository {repository}"
                )

        surface_context = _merge_surface_context(task, entry.get("surface_context"), label)
        stranded = unsatisfied_surface_context(domain, surface_context)
        if stranded:
            raise SuiteError(
                f"{label} cannot satisfy a declared vendor surface, so that vendor would run "
                f"generic against specialist competitors: {'; '.join(sorted(stranded))}"
            )

        reference_answer = entry.get("reference_answer")
        if reference_answer is not None and not isinstance(reference_answer, Mapping):
            raise SuiteError(f"{label}.reference_answer must be a mapping")
        if task.get("judge_rubric") is not None and reference_answer is None:
            raise SuiteError(
                f"{label} is judge-scored, so it must supply a reference_answer for the rubric"
            )
        instance = Instance(
            instance_id=instance_id,
            task_id=task_id,
            domain=domain,
            repository=repository,
            question=question,
            task=task,
            surface_context=surface_context,
            references=references,
            fields=fields,
            reference_answer=reference_answer,
        )
        # Validator flags constrain the ground truth, not just the arm. Checking
        # them here means a manifest that records an unmerged "fixing" pull
        # request fails the offline gate instead of quietly scoring answers
        # against it.
        try:
            assert_instance_contract(instance)
        except ValidationError as exc:
            # One error type out of the loader: callers gate on SuiteError, and
            # a contract violation is a malformed manifest like any other.
            raise SuiteError(str(exc)) from exc
        instances.append(instance)

    missing = sorted(domain_task_ids - covered)
    if missing:
        raise SuiteError(
            f"domain suite {suite_id} is missing an instance for catalog task(s): "
            f"{', '.join(missing)}"
        )
    return DomainSuite(
        suite_id=suite_id,
        domain=domain,
        hypothesis_id=hypothesis_id,
        version=version,
        selection_frame=selection_frame,
        instances=tuple(instances),
    )


def _assert_answer_keys_declared(
    task: Mapping[str, Any],
    references: Sequence[Reference],
    fields: Sequence[FieldExpectation],
    label: str,
) -> None:
    """Every bound answer key must exist in the task's deliverable schema.

    Otherwise an instance can bind ground truth to a key the arm is never asked
    to produce; the check would then be vacuously satisfiable or permanently
    unsatisfiable, and either way the score would not mean what it says.
    """
    schema = task.get("deliverable_schema") or {}
    properties = set(schema.get("properties") or {})
    bound = {reference.answer_key for reference in references}
    bound.update(expectation.answer_key for expectation in fields)
    unknown = sorted(bound - properties)
    if unknown:
        raise SuiteError(
            f"{label} binds answer key(s) absent from the task deliverable schema: "
            f"{', '.join(unknown)}"
        )


def validate_domain_suite(domain: str, root: Path | None = None) -> list[str]:
    """Validate one domain suite offline and return its instance ids."""
    suite = load_domain_suite(domain, root)
    return [instance.instance_id for instance in suite.instances]


def resolve_instance_surfaces(
    instance: Instance,
    capabilities: Mapping[str, Any],
    *,
    unavailable_surfaces: Mapping[str, Iterable[str]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve and record the surface each vendor uses for one instance.

    The SPEC requires the surface used to be recorded in every cell, because the
    surface is part of the claim under test. Recording it here -- offline, from
    the same declarations a live run resolves against -- means a reviewer can
    read the whole vendor-by-task surface matrix without spending a provider
    call, and a live run's records can be diffed against it.
    """
    denied = dict(unavailable_surfaces or {})
    records: list[dict[str, Any]] = []
    for provider_id in sorted(PROVIDERS):
        metadata = capabilities.get(provider_id)
        if metadata is None:
            raise SuiteError(f"missing capability metadata for provider {provider_id!r}")
        try:
            selection = resolve_domain_surface(
                provider_id,
                instance.domain,
                metadata,
                context=instance.surface_context,
                unavailable_surfaces=denied.get(provider_id, ()),
            )
        except DomainSurfaceError as exc:
            raise SuiteError(f"instance {instance.instance_id}: {exc}") from exc
        record = selection.record()
        record.update(
            {
                "instance_id": instance.instance_id,
                "task_id": instance.task_id,
                "repository": instance.repository,
            }
        )
        records.append(record)
    return records


def resolve_suite_surfaces(
    suite: DomainSuite,
    capabilities: Mapping[str, Any],
    *,
    unavailable_surfaces: Mapping[str, Iterable[str]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve the full instance-by-vendor surface matrix for a suite."""
    records: list[dict[str, Any]] = []
    for instance in suite.instances:
        records.extend(
            resolve_instance_surfaces(
                instance, capabilities, unavailable_surfaces=unavailable_surfaces
            )
        )
    return records


# --- live ground-truth verification -----------------------------------------
#
# Offline validation proves a manifest is self-consistent. It cannot prove the
# references still exist, still live in the repository named, or still hold the
# state recorded. That second fact needs the vendor of record -- GitHub -- so it
# is a separate, explicitly-invoked pass rather than part of the offline gate.

GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class GroundTruthCheck:
    instance_id: str
    role: str
    url: str
    resolved: bool
    detail: str


def _api_get(url: str, *, token: str | None, timeout: float) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "sew-dsb-ground-truth",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SuiteError(f"ground-truth resolver unreachable: {exc}") from exc


def _reference_api_url(reference: Reference) -> str:
    repo = f"{GITHUB_API}/repos/{reference.repository}"
    if reference.kind == "pull":
        return f"{repo}/pulls/{reference.number}"
    if reference.kind == "issue":
        return f"{repo}/issues/{reference.number}"
    if reference.kind == "issue_comment":
        return f"{repo}/issues/comments/{reference.comment_id}"
    if reference.kind == "release":
        # Tags, paths and refs are operator-authored free text. Interpolating
        # them raw meant one space in a manifest raised http.client.InvalidURL
        # -- a ValueError, which `_api_get` escalates to a fatal SuiteError --
        # and took down the whole verification run instead of failing the one
        # reference. Percent-encode them so urllib always has a legal URL.
        return f"{repo}/releases/tags/{urllib.parse.quote(reference.tag or '', safe='')}"
    path = urllib.parse.quote(reference.path or "")
    ref = urllib.parse.quote(reference.pinned_ref or "", safe="")
    return f"{repo}/contents/{path}?ref={ref}"


def _check_payload(reference: Reference, payload: Any) -> str | None:
    """Return a mismatch description, or None when the payload agrees."""
    if not isinstance(payload, Mapping):
        return "resolver returned a non-object payload"
    if reference.kind == "pull":
        if payload.get("number") != reference.number:
            return f"resolved number {payload.get('number')}"
        if reference.state == "merged":
            if not payload.get("merged"):
                return "pull request is not merged"
        elif reference.state in {"open", "closed"}:
            # Only `merged` was ever checked, so an instance that declared
            # `state: open` or `state: closed` had its expectation silently
            # dropped: the reference kept verifying after the pull request
            # moved on, which is exactly the drift this pass exists to catch.
            # `merged` is the stronger claim, so a merged pull request does not
            # satisfy a plain `closed` either.
            if payload.get("state") != reference.state:
                return f"resolved state {payload.get('state')}"
            if reference.state == "closed" and payload.get("merged"):
                return "pull request is merged, not closed"
    elif reference.kind == "issue":
        if payload.get("number") != reference.number:
            return f"resolved number {payload.get('number')}"
        if payload.get("pull_request") is not None:
            return "reference is a pull request, not an issue"
        if reference.state and payload.get("state") != (
            "closed" if reference.state == "merged" else reference.state
        ):
            return f"resolved state {payload.get('state')}"
    elif reference.kind == "issue_comment":
        if payload.get("id") != reference.comment_id:
            return f"resolved comment id {payload.get('id')}"
        if reference.number is not None:
            # `kind: issue_comment` covers comments on pull requests too, and
            # GitHub returns those as /pull/<n>#issuecomment-... Accepting only
            # the /issues/ form made live verification reject every legitimate
            # PR comment, blocking operators from adding one to the suite and
            # reporting it as a ground-truth failure.
            html_url = str(payload.get("html_url", ""))
            parents = (f"/issues/{reference.number}#", f"/pull/{reference.number}#")
            if not any(parent in html_url for parent in parents):
                return "comment does not belong to the declared issue or pull request"
    elif reference.kind == "release":
        if payload.get("tag_name") != reference.tag:
            return f"resolved tag {payload.get('tag_name')}"
    elif reference.kind == "blob":
        if payload.get("path") != reference.path:
            return f"resolved path {payload.get('path')}"
    return None


def verify_ground_truth(
    suite: DomainSuite,
    *,
    token: str | None = None,
    timeout_seconds: float = 30.0,
) -> list[GroundTruthCheck]:
    """Re-resolve every declared reference against the live GitHub API."""
    checks: list[GroundTruthCheck] = []
    for instance in suite.instances:
        for reference in instance.references:
            status, payload = _api_get(
                _reference_api_url(reference), token=token, timeout=timeout_seconds
            )
            if status != 200:
                checks.append(
                    GroundTruthCheck(
                        instance.instance_id,
                        reference.role,
                        reference.url,
                        False,
                        f"http_{status}",
                    )
                )
                continue
            mismatch = _check_payload(reference, payload)
            checks.append(
                GroundTruthCheck(
                    instance.instance_id,
                    reference.role,
                    reference.url,
                    mismatch is None,
                    mismatch or "ok",
                )
            )
    return checks


__all__ = [
    "DomainSuite",
    "FieldExpectation",
    "GroundTruthCheck",
    "Instance",
    "Reference",
    "SuiteError",
    "available_domains",
    "load_domain_suite",
    "resolve_instance_surfaces",
    "resolve_suite_surfaces",
    "suite_path",
    "validate_domain_suite",
    "verify_ground_truth",
]
