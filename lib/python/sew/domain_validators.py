"""Deterministic validators for the DSB code_and_pr domain suite.

These validators answer one question per instance: does the arm's deliverable
cite the references the ground truth records, resolving to the right repository
and the right number?

Three properties are deliberate:

* **Binary.** ``ValidationResult.score`` is 1.0 or 0.0 and never in between. A
  pull request number that is close to the right one is a different pull
  request; scoring it as partial credit would let a vendor accumulate a
  head-to-head lead out of answers no engineer could act on, which is precisely
  the failure H1 is supposed to be able to expose.
* **Structural before semantic.** A deliverable that omits a required key fails
  on the key rather than on retrieval, so a formatting failure is not scored as
  an index failure.
* **Near misses are recorded, not rewarded.** When the repository matches and
  the number does not, the check records ``near_miss`` so DSB-08 can show what
  the arm actually found, while the cell still scores zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence
from urllib.parse import unquote

from .schema import SchemaError

if TYPE_CHECKING:
    from .domain_suites import FieldExpectation, Instance, Reference

DEFAULT_HOST = "github.com"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_LINE_ANCHOR = re.compile(r"^L(\d+)(?:-L(\d+))?$")
_COMMENT_ANCHOR = re.compile(r"^issuecomment-(\d+)$")
# The subset of catalog validator kinds this module implements. Other kinds
# belong to the legal, entity-resolution and GTM suites.
SUPPORTED_VALIDATOR_KINDS = frozenset(
    {"github_reference", "release_lineage", "repository_source_locations"}
)


class ValidationError(SchemaError):
    """Raised when an instance cannot be scored deterministically at all."""


@dataclass(frozen=True)
class GitHubUrl:
    """A parsed github.com reference, normalized for comparison."""

    host: str
    repository: str
    kind: str
    number: int | None = None
    comment_id: int | None = None
    tag: str | None = None
    ref: str | None = None
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class Check:
    answer_key: str
    role: str
    passed: bool
    detail: str
    cited: str | None = None
    near_miss: bool = False

    def record(self) -> dict[str, Any]:
        return {
            "answer_key": self.answer_key,
            "role": self.role,
            "passed": self.passed,
            "detail": self.detail,
            "cited": self.cited,
            "near_miss": self.near_miss,
        }


@dataclass(frozen=True)
class ValidationResult:
    instance_id: str
    task_id: str
    validator_kind: str
    passed: bool
    checks: tuple[Check, ...]

    @property
    def score(self) -> float:
        """1.0 or 0.0. There is no partial credit for a wrong reference."""
        return 1.0 if self.passed else 0.0

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            f"{check.answer_key}:{check.detail}" for check in self.checks if not check.passed
        )

    def record(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "task_id": self.task_id,
            "validator_kind": self.validator_kind,
            "passed": self.passed,
            "score": self.score,
            "partial_credit": False,
            "checks": [check.record() for check in self.checks],
            "failures": list(self.failures),
        }


def parse_github_url(url: object) -> GitHubUrl | None:
    """Parse a github.com pull, issue, comment, blob or release URL.

    Returns ``None`` for anything that is not one of those shapes, which the
    callers treat as a failed citation rather than as an exception: an arm is
    free to emit nonsense, and the suite has to score it rather than crash.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    text = url.strip()
    if "://" not in text:
        return None
    scheme, _, remainder = text.partition("://")
    # Schemes are case-insensitive per RFC 3986, and an arm that answers
    # HTTPS://github.com/... has retrieved the right document; scoring it 0.0
    # as an unparseable reference would penalise formatting, not retrieval.
    if scheme.lower() not in {"http", "https"}:
        return None
    remainder, _, fragment = remainder.partition("#")
    remainder, _, _query = remainder.partition("?")
    host, _, rest = remainder.partition("/")
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    # Split on the literal "/" first, then percent-decode each segment. A vendor
    # that correctly encodes `docs/my notes.md` as `docs/my%20notes.md` cited the
    # right file; comparing the encoded form to the ground truth's decoded path
    # scored it path_mismatch. Decoding before the split would be wrong the other
    # way: an encoded `%2F` inside one segment would become a spurious boundary.
    # `unquote`, not `unquote_plus`: `+` is literal in a URL path.
    segments = [unquote(segment) for segment in rest.split("/") if segment]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    repository = f"{owner}/{repo}"
    tail = segments[2:]
    comment_id = _parse_comment_anchor(fragment)
    if not tail:
        return GitHubUrl(host, repository, "repository", comment_id=comment_id)
    section = tail[0]
    if section in {"pull", "pulls", "issues"} and len(tail) >= 2 and tail[1].isdigit():
        kind = "issue" if section == "issues" else "pull"
        return GitHubUrl(host, repository, kind, number=int(tail[1]), comment_id=comment_id)
    if section == "releases" and len(tail) >= 3 and tail[1] == "tag":
        return GitHubUrl(host, repository, "release", tag="/".join(tail[2:]))
    if section in {"blob", "tree"} and len(tail) >= 3:
        start, end = _parse_line_anchor(fragment)
        return GitHubUrl(
            host,
            repository,
            "blob",
            ref=tail[1],
            path="/".join(tail[2:]),
            line_start=start,
            line_end=end,
        )
    if section == "commit" and len(tail) >= 2:
        return GitHubUrl(host, repository, "commit", ref=tail[1])
    return GitHubUrl(host, repository, "repository", comment_id=comment_id)


def _parse_comment_anchor(fragment: str) -> int | None:
    match = _COMMENT_ANCHOR.match(fragment or "")
    return int(match.group(1)) if match else None


def _parse_line_anchor(fragment: str) -> tuple[int | None, int | None]:
    match = _LINE_ANCHOR.match(fragment or "")
    if not match:
        return None, None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return start, end


def match_reference(
    url: object, reference: Reference, *, required_host: str | None = DEFAULT_HOST
) -> tuple[bool, str, bool]:
    """Does ``url`` resolve to exactly the reference recorded as ground truth?

    Returns ``(matched, detail, near_miss)``. ``near_miss`` is set when the arm
    reached the right repository but the wrong reference inside it, which is the
    single most common way a code-search answer is confidently wrong.
    """
    parsed = parse_github_url(url)
    if parsed is None:
        return False, "unparseable_reference", False
    if required_host and parsed.host != required_host:
        return False, f"host_mismatch:{parsed.host}", False
    same_repository = parsed.repository.lower() == reference.repository.lower()
    if not same_repository:
        return False, f"repository_mismatch:{parsed.repository}", False

    if reference.kind in {"pull", "issue", "issue_comment"}:
        # GitHub serves /issues/<n> for a pull request and redirects it to
        # /pull/<n>, so an issues-form citation of a pull request is correct.
        # The reverse is not: /pull/<n> for a plain issue is a 404, so it is a
        # genuinely wrong reference rather than an alternate spelling.
        # The redirect is one-way, so the allowed forms depend on what the
        # parent ACTUALLY is -- not merely on whether this is a comment. A
        # comment on a plain issue cited as /pull/<n> is a 404 exactly like the
        # issue itself would be, and the permissive set was handing it a
        # perfect score for a broken link.
        if reference.kind == "issue_comment":
            # Ask the parser what the parent is rather than sniffing the URL
            # for "/pull/": GitHub serves the plural "/pulls/<n>" form too, and
            # a manifest that spells it that way would have been read as a
            # plain issue and rejected every correct "/pull/" citation.
            parent = parse_github_url(reference.url)
            parent_is_pull = parent is not None and parent.kind == "pull"
            allowed_kinds = {"pull", "issue"} if parent_is_pull else {"issue"}
        else:
            allowed_kinds = {"pull", "issue"} if reference.kind != "issue" else {"issue"}
        if parsed.kind not in allowed_kinds:
            return False, f"reference_kind_mismatch:{parsed.kind}", True
        if parsed.number != reference.number:
            return False, f"number_mismatch:{parsed.number}", True
        if reference.kind == "issue_comment":
            if parsed.comment_id is None:
                return False, "missing_comment_anchor", True
            if parsed.comment_id != reference.comment_id:
                return False, f"comment_mismatch:{parsed.comment_id}", True
        return True, "ok", False

    if reference.kind == "release":
        if parsed.kind != "release":
            return False, f"reference_kind_mismatch:{parsed.kind}", True
        if parsed.tag != reference.tag:
            return False, f"tag_mismatch:{parsed.tag}", True
        return True, "ok", False

    if reference.kind == "blob":
        if parsed.kind != "blob":
            return False, f"reference_kind_mismatch:{parsed.kind}", True
        if parsed.path != reference.path:
            return False, f"path_mismatch:{parsed.path}", True
        if parsed.ref != reference.pinned_ref:
            return False, f"ref_mismatch:{parsed.ref}", True
        if reference.line_range is not None:
            if parsed.line_start is None:
                return False, "missing_line_anchor", True
            start, end = reference.line_range
            if not (start <= parsed.line_start <= end):
                return False, f"line_out_of_range:{parsed.line_start}", True
            # The END anchor was unchecked, so #L660-L99999 scored a perfect
            # match for starting in the right block while spanning thousands
            # of irrelevant lines. A citation that cannot be narrowed to the
            # cited range is not a precise citation.
            if parsed.line_end is not None and not (start <= parsed.line_end <= end):
                return False, f"line_span_too_broad:{parsed.line_end}", True
        return True, "ok", False

    return False, f"unsupported_reference_kind:{reference.kind}", False


def assert_instance_contract(instance: Instance) -> None:
    """Assert the manifest satisfies the flags its catalog validator declares.

    These flags constrain the *ground truth*, not the arm: ``require_merged_pr``
    means the recorded pull request must actually be merged, so that an arm
    which matches ground truth has necessarily cited a merged pull request. If
    the flags were only read at scoring time, a manifest recording an unmerged
    pull request would score answers against it happily and the declared
    contract would be decorative.
    """
    validator = instance.validator
    if validator is None:
        return
    kind = validator.get("kind")
    if kind not in SUPPORTED_VALIDATOR_KINDS:
        raise ValidationError(
            f"instance {instance.instance_id} declares validator kind {kind!r}, which this "
            f"domain's validators do not implement"
        )
    roles = {reference.role for reference in instance.references}
    if validator.get("required_host") not in (None, DEFAULT_HOST) and kind == "github_reference":
        raise ValidationError(
            f"instance {instance.instance_id} declares an unsupported required_host"
        )
    if validator.get("require_merged_pr"):
        merged = [
            reference
            for reference in instance.references
            if reference.kind == "pull" and reference.state == "merged"
        ]
        if not merged:
            raise ValidationError(
                f"instance {instance.instance_id} declares require_merged_pr but records no "
                "pull-request reference in state 'merged'"
            )
    if validator.get("require_issue") and "issue" not in roles:
        raise ValidationError(
            f"instance {instance.instance_id} declares require_issue but records no issue reference"
        )
    if validator.get("require_closed_issue"):
        closed = [
            reference
            for reference in instance.references
            if reference.kind == "issue" and reference.state == "closed"
        ]
        if not closed:
            raise ValidationError(
                f"instance {instance.instance_id} declares require_closed_issue but records no "
                "issue reference in state 'closed'"
            )
    if validator.get("require_comment_anchor") and not [
        reference for reference in instance.references if reference.kind == "issue_comment"
    ]:
        raise ValidationError(
            f"instance {instance.instance_id} declares require_comment_anchor but records no "
            "comment reference"
        )
    if validator.get("require_commit_pins"):
        unpinned = [
            reference.role
            for reference in instance.references
            if reference.kind == "blob" and not _COMMIT_SHA.match(reference.pinned_ref or "")
        ]
        if unpinned:
            raise ValidationError(
                f"instance {instance.instance_id} declares require_commit_pins but records "
                f"unpinned blob reference(s): {sorted(unpinned)}"
            )
    minimum = validator.get("minimum_call_sites")
    # `bool` subclasses `int`: without the exclusion `minimum_call_sites: true`
    # reads as a configured floor of 1 instead of an unconfigured key.
    if isinstance(minimum, int) and not isinstance(minimum, bool):
        call_sites = [
            reference for reference in instance.references if reference.role == "call_site"
        ]
        if len(call_sites) < minimum:
            raise ValidationError(
                f"instance {instance.instance_id} declares minimum_call_sites={minimum} but "
                f"records {len(call_sites)}"
            )
    if validator.get("require_release_tag") and not [
        reference for reference in instance.references if reference.kind == "release"
    ]:
        raise ValidationError(
            f"instance {instance.instance_id} declares require_release_tag but records no release"
        )
    if validator.get("require_change_reference") and "change" not in roles:
        raise ValidationError(
            f"instance {instance.instance_id} declares require_change_reference but records no "
            "change reference"
        )


def _is_array_key(instance: Instance, answer_key: str) -> bool:
    """Does the task's deliverable schema declare this key as an array?"""
    properties = (instance.task.get("deliverable_schema") or {}).get("properties") or {}
    declared = properties.get(answer_key)
    return isinstance(declared, Mapping) and declared.get("type") == "array"


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_key_checks(instance: Instance, answer: Mapping[str, Any]) -> list[Check]:
    schema = instance.task.get("deliverable_schema") or {}
    checks: list[Check] = []
    for key in schema.get("required") or ():
        value = answer.get(key)
        present = value not in (None, "", [], {}) and (
            not isinstance(value, str) or bool(value.strip())
        )
        checks.append(
            Check(
                answer_key=str(key),
                role="deliverable_schema",
                passed=present,
                detail="ok" if present else "missing_required_key",
            )
        )
    return checks


def _single_reference_check(
    instance: Instance, answer: Mapping[str, Any], reference: Reference, required_host: str | None
) -> Check:
    cited = answer.get(reference.answer_key)
    matched, detail, near_miss = match_reference(cited, reference, required_host=required_host)
    return Check(
        answer_key=reference.answer_key,
        role=reference.role,
        passed=matched,
        detail=detail,
        cited=_as_text(cited) or None,
        near_miss=near_miss,
    )


def _list_reference_checks(
    instance: Instance,
    answer: Mapping[str, Any],
    references: Sequence[Reference],
    required_host: str | None,
    minimum: int,
) -> list[Check]:
    """Match a list-valued answer key against several accepted references.

    Each declared reference may be satisfied at most once, so repeating one
    correct link cannot stand in for the second distinct location the task asks
    for.
    """
    answer_key = references[0].answer_key
    cited = answer.get(answer_key)
    values = [item for item in cited if isinstance(item, str)] if isinstance(cited, list) else []
    if not isinstance(cited, list):
        return [
            Check(
                answer_key=answer_key,
                role=references[0].role,
                passed=False,
                detail="expected_a_list",
                cited=_as_text(cited) or None,
            )
        ]
    checks: list[Check] = []
    remaining = list(references)
    matched_count = 0
    for value in values:
        for candidate in list(remaining):
            matched, detail, _near = match_reference(value, candidate, required_host=required_host)
            if matched:
                remaining.remove(candidate)
                matched_count += 1
                checks.append(
                    Check(
                        answer_key=answer_key,
                        role=candidate.role,
                        passed=True,
                        detail=detail,
                        cited=value,
                    )
                )
                break
        else:
            near = any(
                match_reference(value, candidate, required_host=required_host)[2]
                for candidate in references
            )
            checks.append(
                Check(
                    answer_key=answer_key,
                    role=references[0].role,
                    passed=False,
                    detail="unmatched_reference",
                    cited=value,
                    near_miss=near,
                )
            )
    checks.append(
        Check(
            answer_key=answer_key,
            role=f"{references[0].role}_count",
            passed=matched_count >= minimum,
            detail="ok" if matched_count >= minimum else f"matched_{matched_count}_of_{minimum}",
        )
    )
    return checks


def _field_check(expectation: FieldExpectation, answer: Mapping[str, Any]) -> Check:
    value = answer.get(expectation.answer_key)
    if expectation.match == "exact":
        text = _as_text(value)
        passed = text == expectation.value
        detail = "ok" if passed else f"value_mismatch:{text or None}"
        return Check(expectation.answer_key, "field", passed, detail, cited=text or None)
    items = (
        {item.strip() for item in (value or ()) if isinstance(item, str) and item.strip()}
        if isinstance(value, (list, tuple))
        else set()
    )
    missing = sorted(set(expectation.values) - items)
    present_forbidden = sorted(set(expectation.forbidden) & items)
    if missing:
        return Check(expectation.answer_key, "field", False, f"missing:{','.join(missing)}")
    if present_forbidden:
        return Check(
            expectation.answer_key,
            "field",
            False,
            f"forbidden_present:{','.join(present_forbidden)}",
        )
    return Check(expectation.answer_key, "field", True, "ok")


def score_instance(instance: Instance, answer: Mapping[str, Any]) -> ValidationResult:
    """Score one deliverable against one instance's recorded ground truth."""
    validator = instance.validator
    if validator is None:
        raise ValidationError(
            f"instance {instance.instance_id} is judge-scored ("
            f"{(instance.rubric or {}).get('rubric_id')}); it has no deterministic validator"
        )
    kind = str(validator.get("kind"))
    if kind not in SUPPORTED_VALIDATOR_KINDS:
        raise ValidationError(f"unsupported validator kind for this domain: {kind}")
    if not isinstance(answer, Mapping):
        raise ValidationError(f"instance {instance.instance_id}: answer must be a mapping")
    required_host = validator.get("required_host", DEFAULT_HOST)

    checks = _required_key_checks(instance, answer)
    by_key: dict[str, list[Reference]] = {}
    for reference in instance.references:
        by_key.setdefault(reference.answer_key, []).append(reference)
    for answer_key, references in by_key.items():
        # Dispatch on what the task asks the arm to produce, not on how many
        # references happen to be recorded. A one-reference binding on an array
        # key would otherwise be matched as a bare string and score a correct
        # list answer as unparseable.
        if not _is_array_key(instance, answer_key):
            checks.append(_single_reference_check(instance, answer, references[0], required_host))
            continue
        minimum = validator.get("minimum_call_sites")
        floor = (
            minimum
            if isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and references[0].role == "call_site"
            else len(references)
        )
        checks.extend(_list_reference_checks(instance, answer, references, required_host, floor))
    for expectation in instance.fields:
        checks.append(_field_check(expectation, answer))

    return ValidationResult(
        instance_id=instance.instance_id,
        task_id=instance.task_id,
        validator_kind=kind,
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
    )


__all__ = [
    "DEFAULT_HOST",
    "SUPPORTED_VALIDATOR_KINDS",
    "Check",
    "GitHubUrl",
    "ValidationError",
    "ValidationResult",
    "assert_instance_contract",
    "match_reference",
    "parse_github_url",
    "score_instance",
]
