from __future__ import annotations

import copy
from typing import Any, Callable

import pytest
import yaml

from conftest import CATALOG_ROOT

from sew.catalog import load_domain_tasks
from sew.domain_suites import (
    SuiteError,
    resolve_instance_surfaces,
    available_domains,
    load_domain_suite,
    resolve_suite_surfaces,
    suite_path,
    validate_domain_suite,
)
from sew.domain_surfaces import (
    DOWNGRADE_UNDECLARED,
    GENERIC_SURFACE,
    PROVIDERS,
    unsatisfied_surface_context,
)
from sew.providers import make_provider

DOMAIN = "code_and_pr"
EXPECTED_TASK_IDS = {
    "code-fixing-pr",
    "code-error-to-issue",
    "code-api-implementation",
    "code-breaking-release",
    "code-deprecation-migration",
    "code-closed-issue-only",
}


def _capabilities() -> dict[str, Any]:
    return {
        provider_id: make_provider(provider_id, live_enabled=False).capabilities
        for provider_id in PROVIDERS
    }


def _suite_root(tmp_path, mutate: Callable[[dict], None] | None = None):
    """Copy the real catalogs plus the code_and_pr suite, optionally mutated."""
    source = CATALOG_ROOT / "catalogs" / "domains"
    target = tmp_path / "catalogs" / "domains"
    (target / "suites").mkdir(parents=True)
    for name in ("claims.yaml", "tasks.yaml"):
        (target / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    document = yaml.safe_load((source / "suites" / f"{DOMAIN}.yaml").read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(document)
    (target / "suites" / f"{DOMAIN}.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return tmp_path


def _instance(document: dict, task_id: str) -> dict:
    for entry in document["instances"]:
        if entry["task_id"] == task_id:
            return entry
    raise AssertionError(f"no instance for {task_id}")


def test_code_and_pr_suite_covers_every_catalog_task_once() -> None:
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)

    assert suite.suite_id == "code-and-pr-v1"
    assert suite.hypothesis_id == "H1"
    assert {instance.task_id for instance in suite.instances} == EXPECTED_TASK_IDS
    assert len(suite.instances) == len(EXPECTED_TASK_IDS)
    assert available_domains(CATALOG_ROOT) == [DOMAIN]
    assert validate_domain_suite(DOMAIN, CATALOG_ROOT) == [
        instance.instance_id for instance in suite.instances
    ]


def test_expected_loss_task_is_present_with_an_instance() -> None:
    """The domain must keep a task its claimant is expected to lose.

    Without it, a supported H1 could be an artifact of task selection. The
    designation is the DSB-01 catalog's; this suite must carry an instance for
    whichever task it names, and must also carry the SPEC's poorly-documented
    repository task.
    """
    tasks = load_domain_tasks(CATALOG_ROOT)
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)
    covered = {instance.task_id for instance in suite.instances}

    expected_loss = [
        task_id
        for task_id, task in tasks.items()
        if task.get("domain") == DOMAIN and task.get("expected_claimant_outcome") == "loss"
    ]
    assert expected_loss, "code_and_pr must declare an expected-loss task"
    assert set(expected_loss) <= covered
    assert suite.selection_frame["expected_loss_task_id"] in expected_loss
    # The poorly-documented-repository task is the SPEC's sixth shape and is
    # never dropped, whether or not it carries the loss designation.
    assert "code-closed-issue-only" in covered


def test_every_instance_records_a_surface_for_every_vendor() -> None:
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)

    records = resolve_suite_surfaces(suite, _capabilities())

    assert len(records) == len(suite.instances) * len(PROVIDERS)
    assert {(record["instance_id"], record["provider_id"]) for record in records} == {
        (instance.instance_id, provider_id)
        for instance in suite.instances
        for provider_id in PROVIDERS
    }
    for record in records:
        assert record["domain"] == DOMAIN
        assert record["surface_id"]
        assert isinstance(record["generic_surface"], bool)
        assert record["task_id"] in EXPECTED_TASK_IDS


def test_surface_matrix_marks_generic_arms_rather_than_failing_them() -> None:
    """Exa ships no code surface, so its cells are generic and say why.

    A vendor without a domain surface must be recorded `generic_surface`, not
    failed, and the claimant must reach its specialist arm on every instance --
    otherwise the head-to-head measures surface access rather than retrieval.
    """
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)

    records = resolve_suite_surfaces(suite, _capabilities())
    by_provider: dict[str, set[str]] = {}
    for record in records:
        by_provider.setdefault(record["provider_id"], set()).add(record["surface_id"])

    assert by_provider["firecrawl"] == {"firecrawl.category.developer"}
    assert by_provider["parallel-web"] == {"parallel.search.source_policy"}
    assert by_provider["exa"] == {GENERIC_SURFACE}
    exa_records = [record for record in records if record["provider_id"] == "exa"]
    assert all(record["downgrade_reason"] == DOWNGRADE_UNDECLARED for record in exa_records)
    assert all(record["generic_surface"] for record in exa_records)


def test_instance_surface_context_extends_rather_than_replaces_the_task_policy() -> None:
    """The Pillow instance adds a docs host without losing the code hosts."""
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)

    fixing = suite.instance("httpx-query-bracket-escaping")
    pillow = suite.instance("pillow-freetypefont-getsize")

    assert fixing.surface_context["source_policy"]["include_domains"] == [
        "github.com",
        "gitlab.com",
    ]
    assert pillow.surface_context["source_policy"]["include_domains"] == [
        "github.com",
        "gitlab.com",
        "pillow.readthedocs.io",
    ]


def test_every_instance_uses_a_distinct_repository() -> None:
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)

    repositories = [instance.repository for instance in suite.instances]
    organizations = [repository.split("/")[0] for repository in repositories]

    assert len(set(repositories)) == len(repositories)
    assert len(set(organizations)) == len(organizations)
    assert suite.selection_frame["distinct_organizations"] == len(set(organizations))


def test_suite_rejects_a_reference_whose_url_disagrees_with_its_number(tmp_path) -> None:
    def mutate(document: dict) -> None:
        reference = _instance(document, "code-fixing-pr")["expected_references"][0]
        reference["url"] = "https://github.com/encode/httpx/pull/2702"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="url points at #2702"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_reference_pointing_at_another_repository(tmp_path) -> None:
    def mutate(document: dict) -> None:
        reference = _instance(document, "code-fixing-pr")["expected_references"][0]
        reference["repository"] = "encode/httpcore"
        reference["url"] = "https://github.com/encode/httpcore/pull/2701"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="not the instance repository"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_missing_task_instance(tmp_path) -> None:
    def mutate(document: dict) -> None:
        document["instances"] = [
            entry for entry in document["instances"] if entry["task_id"] != "code-closed-issue-only"
        ]

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="code-closed-issue-only"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_reused_repository(tmp_path) -> None:
    def mutate(document: dict) -> None:
        entry = _instance(document, "code-breaking-release")
        entry["repository"] = "encode/httpx"
        for reference in entry["expected_references"]:
            reference["repository"] = "encode/httpx"
        entry["expected_references"][0]["url"] = (
            "https://github.com/encode/httpx/releases/tag/2.1.0"
        )
        entry["expected_references"][1]["url"] = "https://github.com/encode/httpx/pull/261"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="reuses repository"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_an_instance_that_strands_a_vendor_on_the_generic_arm(tmp_path) -> None:
    """Emptying a vendor's required context decides the comparison silently.

    Parallel's code_and_pr surface needs a non-empty source policy. An instance
    supplying an empty one keeps the key present -- so a key-presence check
    passes -- while the resolver demotes Parallel to `generic_surface` and
    Firecrawl keeps its developer category. The honest `generic_surface`
    marking would then make the rigged run look correctly reported.
    """

    def mutate(document: dict) -> None:
        _instance(document, "code-fixing-pr")["surface_context"] = {"source_policy": {}}

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError):
        load_domain_suite(DOMAIN, root)
    # The guard that makes the empty case specifically visible, asserted
    # directly: an empty policy keeps the key and still demotes the vendor.
    assert unsatisfied_surface_context(DOMAIN, {"source_policy": {}}) == [
        "parallel-web/code_and_pr needs ['source_policy']"
    ]
    assert unsatisfied_surface_context(DOMAIN, {}) == [
        "parallel-web/code_and_pr needs ['source_policy']"
    ]
    assert unsatisfied_surface_context(DOMAIN, {"source_policy": {"include_domains": ["a"]}}) == []


def test_suite_rejects_an_instance_that_narrows_the_task_source_policy(tmp_path) -> None:
    """Instances may widen a source policy; narrowing it rigs the task.

    An instance that drops a host the task allows binds the policy-bound arm to
    a corpus chosen per-instance, so a miss says nothing about that vendor's
    index.
    """

    def mutate(document: dict) -> None:
        _instance(document, "code-fixing-pr")["surface_context"] = {
            "source_policy": {"include_domains": ["github.com"]}
        }

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="narrows the task source policy"):
        load_domain_suite(DOMAIN, root)


def test_suite_accepts_an_instance_that_widens_the_task_source_policy() -> None:
    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)

    widened = suite.instance("pillow-freetypefont-getsize").surface_context["source_policy"]

    assert set(widened["include_domains"]) > {"github.com", "gitlab.com"}


def test_suite_rejects_instances_selected_with_a_vendor_index(tmp_path) -> None:
    def mutate(document: dict) -> None:
        document["selection_frame"]["vendor_indexes_consulted"] = "firecrawl"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="vendor_indexes_consulted"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_an_unmerged_pull_request_as_a_fix(tmp_path) -> None:
    """`require_merged_pr` constrains the ground truth, not only the answer."""

    def mutate(document: dict) -> None:
        reference = _instance(document, "code-fixing-pr")["expected_references"][0]
        reference["state"] = "open"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="require_merged_pr"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_closed_issue_task_without_a_comment_anchor(tmp_path) -> None:
    def mutate(document: dict) -> None:
        entry = _instance(document, "code-closed-issue-only")
        entry["expected_references"] = [
            reference
            for reference in entry["expected_references"]
            if reference["kind"] != "issue_comment"
        ]

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="require_comment_anchor"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_an_answer_key_absent_from_the_deliverable_schema(tmp_path) -> None:
    def mutate(document: dict) -> None:
        reference = _instance(document, "code-fixing-pr")["expected_references"][0]
        reference["answer_key"] = "merge_commit_url"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="absent from the task deliverable schema"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_judge_scored_instance_without_a_reference_answer(tmp_path) -> None:
    def mutate(document: dict) -> None:
        _instance(document, "code-deprecation-migration").pop("reference_answer")

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="reference_answer"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_fewer_call_sites_than_the_validator_requires(tmp_path) -> None:
    def mutate(document: dict) -> None:
        entry = _instance(document, "code-api-implementation")
        entry["expected_references"] = [
            reference
            for reference in entry["expected_references"]
            if reference["role"] != "call_site"
        ] + [
            reference
            for reference in entry["expected_references"]
            if reference["role"] == "call_site"
        ][:1]

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="minimum_call_sites"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_an_unpinned_source_location(tmp_path) -> None:
    def mutate(document: dict) -> None:
        reference = _instance(document, "code-api-implementation")["expected_references"][0]
        reference.pop("commit")
        reference["ref"] = "main"
        reference["url"] = "https://github.com/psf/requests/blob/main/src/requests/utils.py#L660"

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="require_commit_pins"):
        load_domain_suite(DOMAIN, root)


def test_unknown_domain_suite_is_refused() -> None:
    with pytest.raises(SuiteError, match="no suite manifest"):
        load_domain_suite("legal", CATALOG_ROOT)
    assert not suite_path("legal", CATALOG_ROOT).exists()


def test_suite_manifest_is_stable_under_reload() -> None:
    first = load_domain_suite(DOMAIN, CATALOG_ROOT)
    second = load_domain_suite(DOMAIN, CATALOG_ROOT)

    assert [instance.instance_id for instance in first.instances] == [
        instance.instance_id for instance in second.instances
    ]
    assert copy.deepcopy(dict(first.selection_frame)) == dict(second.selection_frame)


class _RecordingTransport:
    """Records the request and answers every call with an empty result set."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers, json_body, timeout_seconds):
        from sew.providers import HttpResponse

        self.calls.append({"url": url, "json_body": dict(json_body or {})})
        return HttpResponse(200, {"data": [], "results": []})


@pytest.mark.parametrize("provider_id", ["firecrawl", "parallel-web", "exa"])
def test_live_call_records_the_same_surface_the_offline_matrix_predicts(provider_id: str) -> None:
    """The recorded surface must be the same fact offline and on the wire.

    "Each task records the surface used per vendor" is only worth anything if
    the matrix a reviewer reads offline is the matrix a run actually executes.
    Driving one instance's context through the real adapter and diffing the
    persisted `domain_surface` record against the offline prediction is what
    makes the two the same claim rather than two independent guesses.
    """
    from sew.providers import PROVIDER_CREDENTIALS, CredentialResolver, make_provider

    suite = load_domain_suite(DOMAIN, CATALOG_ROOT)
    instance = suite.instance("httpx-query-bracket-escaping")
    credential_env = PROVIDER_CREDENTIALS[provider_id][0]
    transport = _RecordingTransport()
    provider = make_provider(
        provider_id,
        credential_resolver=CredentialResolver(environ={credential_env: "secret"}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search_domain(
        instance.question,
        domain=instance.domain,
        run_id="test-run",
        surface_context=instance.surface_context,
    )

    predicted = next(
        record
        for record in resolve_instance_surfaces(instance, _capabilities())
        if record["provider_id"] == provider_id
    )
    recorded = result.call_record["response"]["domain_surface"]

    assert recorded["surface_id"] == predicted["surface_id"]
    assert recorded["generic_surface"] == predicted["generic_surface"]
    assert recorded["downgrade_reason"] == predicted["downgrade_reason"]
    assert recorded["option_keys"] == predicted["option_keys"]
    assert transport.calls, "the adapter must have issued a request"


def _comment_reference(url: str):
    from sew.domain_suites import Reference

    return Reference(
        answer_key="issue_url",
        role="primary",
        kind="issue_comment",
        repository="encode/httpx",
        url=url,
        number=3103,
        comment_id=1234567890,
    )


def test_ground_truth_verification_accepts_a_pull_request_comment() -> None:
    """`kind: issue_comment` covers comments on pull requests too.

    GitHub returns those as /pull/<n>#issuecomment-..., and accepting only the
    /issues/ form made live verification reject every legitimate PR comment --
    reporting a false ground-truth failure and blocking operators from adding
    one to the suite.
    """
    from sew.domain_suites import _check_payload

    reference = _comment_reference(
        "https://github.com/encode/httpx/pull/3103#issuecomment-1234567890"
    )
    payload = {
        "id": 1234567890,
        "html_url": "https://github.com/encode/httpx/pull/3103#issuecomment-1234567890",
    }

    assert _check_payload(reference, payload) is None


def test_ground_truth_verification_still_rejects_a_foreign_parent() -> None:
    # The widened guard must not stop catching a comment on the wrong thread.
    from sew.domain_suites import _check_payload

    reference = _comment_reference(
        "https://github.com/encode/httpx/issues/3103#issuecomment-1234567890"
    )
    payload = {
        "id": 1234567890,
        "html_url": "https://github.com/encode/httpx/issues/9999#issuecomment-1234567890",
    }

    assert _check_payload(reference, payload) is not None


def test_manifest_url_line_anchor_must_agree_with_line_range() -> None:
    """The example URL and the structured bounds are two halves of one claim.

    A manifest declaring `line_range: [100, 110]` beside a URL ending `#L200`
    loaded clean, so the mismatch surfaced only by hand.
    """
    from sew.domain_suites import Reference, _assert_url_agrees

    reference = Reference(
        answer_key="evidence_urls",
        role="primary",
        kind="blob",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/blob/abc123/httpx/_urls.py#L200",
        path="httpx/_urls.py",
        commit="abc123",
        line_range=(100, 110),
    )

    with pytest.raises(SuiteError, match="outside line_range"):
        _assert_url_agrees(reference, "instance.evidence_urls[0]")


def test_manifest_line_range_requires_a_line_anchor_on_the_url() -> None:
    """A loader looser than the scorer admits an unscoreable reference answer.

    `match_reference` rejects a blob citation with no line anchor when the
    reference declares `line_range` (`missing_line_anchor`), so a manifest whose
    own example URL omits the anchor records a reference answer that can never
    be graded correct.
    """
    from sew.domain_suites import Reference, _assert_url_agrees

    reference = Reference(
        answer_key="implementation_url",
        role="implementation",
        kind="blob",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/blob/abc123/httpx/_urls.py",
        path="httpx/_urls.py",
        commit="abc123",
        line_range=(100, 110),
    )

    with pytest.raises(SuiteError, match="no line anchor"):
        _assert_url_agrees(reference, "instance.expected_references[0]")


def test_manifest_line_anchor_may_not_span_beyond_line_range() -> None:
    """The end anchor is half the claim, and the scorer checks it too.

    `#L105-L999` starts inside `[100, 110]` and spans hundreds of lines past it,
    which the runtime scorer rejects as `line_span_too_broad`.
    """
    from sew.domain_suites import Reference, _assert_url_agrees

    reference = Reference(
        answer_key="implementation_url",
        role="implementation",
        kind="blob",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/blob/abc123/httpx/_urls.py#L105-L999",
        path="httpx/_urls.py",
        commit="abc123",
        line_range=(100, 110),
    )

    with pytest.raises(SuiteError, match="line_span_too_broad"):
        _assert_url_agrees(reference, "instance.expected_references[0]")


def test_manifest_line_anchor_inside_the_bounds_still_loads() -> None:
    """The tightened check must not reject the honest case it exists to protect."""
    from sew.domain_suites import Reference, _assert_url_agrees

    reference = Reference(
        answer_key="implementation_url",
        role="implementation",
        kind="blob",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/blob/abc123/httpx/_urls.py#L100-L110",
        path="httpx/_urls.py",
        commit="abc123",
        line_range=(100, 110),
    )

    _assert_url_agrees(reference, "instance.expected_references[0]")


def test_manifest_rejects_a_line_range_the_scorer_can_never_enforce() -> None:
    """`line_range` on a non-blob reference is precision the scorer never checks.

    `match_reference` enforces line bounds only for blob references, so a
    `line_range` beside a pull reference reads as checked ground truth while
    constraining nothing.
    """
    from sew.domain_suites import Reference, _require_kind_fields

    reference = Reference(
        answer_key="pull_request_url",
        role="pull_request",
        kind="pull",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/pull/3103",
        number=3103,
        line_range=(100, 110),
    )

    with pytest.raises(SuiteError, match="may not declare a line_range"):
        _require_kind_fields(reference, "instance.expected_references[0]")


def _pull_reference(state: str | None):
    from sew.domain_suites import Reference

    return Reference(
        answer_key="pull_request_url",
        role="pull_request",
        kind="pull",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/pull/3103",
        number=3103,
        state=state,
    )


def test_a_pull_request_that_left_its_declared_state_fails_verification() -> None:
    """Only `merged` was checked, so `open`/`closed` verified vacuously.

    A reference declaring `state: open` kept passing after the pull request
    closed, which is precisely the ground-truth drift this pass exists to
    catch.
    """
    from sew.domain_suites import _check_payload

    assert _check_payload(_pull_reference("open"), {"number": 3103, "state": "open"}) is None
    assert _check_payload(_pull_reference("open"), {"number": 3103, "state": "closed"}) is not None
    assert (
        _check_payload(
            _pull_reference("closed"), {"number": 3103, "state": "closed", "merged": False}
        )
        is None
    )
    # `merged` is the stronger claim and has its own state value, so a merged
    # pull request does not satisfy a plain `closed`.
    assert (
        _check_payload(
            _pull_reference("closed"), {"number": 3103, "state": "closed", "merged": True}
        )
        is not None
    )
    # The merged path is unchanged.
    assert (
        _check_payload(
            _pull_reference("merged"), {"number": 3103, "state": "closed", "merged": True}
        )
        is None
    )


def test_reference_api_urls_percent_encode_operator_authored_text() -> None:
    """Paths, refs and tags are free text and must not be interpolated raw.

    One space in a manifest made urllib raise http.client.InvalidURL -- a
    ValueError, which the resolver escalates to a fatal SuiteError -- killing
    the whole verification run instead of failing the single reference.
    """
    from sew.domain_suites import Reference, _reference_api_url

    blob = Reference(
        answer_key="evidence_urls",
        role="implementation",
        kind="blob",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/blob/main/docs/my%20notes.md",
        path="docs/my notes.md",
        ref="feature/my branch",
    )
    url = _reference_api_url(blob)
    assert " " not in url
    assert url.endswith("/contents/docs/my%20notes.md?ref=feature%2Fmy%20branch")

    release = Reference(
        answer_key="release_url",
        role="release",
        kind="release",
        repository="encode/httpx",
        url="https://github.com/encode/httpx/releases/tag/v1%200",
        tag="v1 0",
    )
    assert _reference_api_url(release).endswith("/releases/tags/v1%200")


def test_suite_rejects_a_boolean_line_bound(tmp_path) -> None:
    """`bool` subclasses `int`, so `[true, 679]` would have loaded as `[1, 679]`.

    The loader's positive-integer guard was `isinstance(bound, int)`, which is
    `True` for `True`. A malformed bound silently became line 1 and the
    reference then claimed scoreable ground truth it never had.
    """

    def mutate(document: dict) -> None:
        reference = _instance(document, "code-api-implementation")["expected_references"][0]
        reference["line_range"] = [True, 679]

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="line_range must be"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_boolean_reference_number(tmp_path) -> None:
    """Same `bool`-is-`int` gap on the identifier fields, not just line bounds."""

    def mutate(document: dict) -> None:
        _instance(document, "code-fixing-pr")["expected_references"][0]["number"] = True

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="number must be a positive integer"):
        load_domain_suite(DOMAIN, root)


def test_suite_rejects_a_boolean_comment_id(tmp_path) -> None:
    """A boolean `comment_id` would have pinned the ground truth to comment 1."""

    def mutate(document: dict) -> None:
        for reference in _instance(document, "code-closed-issue-only")["expected_references"]:
            if "comment_id" in reference:
                reference["comment_id"] = True
                return
        raise AssertionError("no comment reference to mutate")

    root = _suite_root(tmp_path, mutate)

    with pytest.raises(SuiteError, match="comment_id must be a positive integer"):
        load_domain_suite(DOMAIN, root)


def test_a_plain_issue_cannot_claim_to_be_merged(tmp_path) -> None:
    """Only a pull request merges; live verification reads `merged` on an
    issue as `closed`, so the nonsense claim must be refused where it is written."""

    def merged_issue(document):
        for entry in document["instances"]:
            for reference in entry["expected_references"]:
                if reference.get("kind") == "issue":
                    reference["state"] = "merged"
                    return

    with pytest.raises(SuiteError, match="kind issue cannot be state merged"):
        load_domain_suite(DOMAIN, _suite_root(tmp_path, merged_issue))


def test_the_task_catalog_is_parsed_once_per_load(monkeypatch) -> None:
    from sew import catalog

    reads: list[str] = []
    real = catalog._load_mapping

    def counting(path, label):
        reads.append(str(path))
        return real(path, label)

    monkeypatch.setattr(catalog, "_load_mapping", counting)
    catalog.load_domain_tasks(CATALOG_ROOT)

    assert sum(p.endswith("tasks.yaml") for p in reads) == 1
