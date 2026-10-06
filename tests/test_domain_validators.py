from __future__ import annotations

import pytest

from conftest import CATALOG_ROOT

from sew.domain_suites import load_domain_suite
from sew.domain_validators import (
    ValidationError,
    match_reference,
    parse_github_url,
    score_instance,
)

DOMAIN = "code_and_pr"

CORRECT_ANSWERS: dict[str, dict] = {
    "httpx-query-bracket-escaping": {
        "repository": "encode/httpx",
        "pull_request_url": "https://github.com/encode/httpx/pull/2701",
        "issue_url": "https://github.com/encode/httpx/issues/2694",
        "evidence_urls": ["https://github.com/encode/httpx/blob/0.24.1/CHANGELOG.md"],
    },
    "urllib3-negative-read-amt": {
        "issue_url": "https://github.com/urllib3/urllib3/issues/3122",
        "affected_versions": ["2.0.4", "2.2.1"],
        "fixed_version": "2.2.2",
        "evidence_urls": ["https://github.com/urllib3/urllib3/pull/3356"],
    },
    "requests-requote-uri-call-sites": {
        "implementation_url": (
            "https://github.com/psf/requests/blob/"
            "0e322af87745eff34caffe4df68456ebc20d9068/src/requests/utils.py#L660-L679"
        ),
        "call_site_urls": [
            "https://github.com/psf/requests/blob/"
            "0e322af87745eff34caffe4df68456ebc20d9068/src/requests/models.py#L480",
            "https://github.com/psf/requests/blob/"
            "0e322af87745eff34caffe4df68456ebc20d9068/src/requests/sessions.py#L217",
        ],
        "explanation": "PreparedRequest.prepare_url and SessionRedirectMixin.resolve_redirects",
    },
    "markupsafe-soft-unicode-removal": {
        "release": "2.1.0",
        "release_url": "https://github.com/pallets/markupsafe/releases/tag/2.1.0",
        "change_url": "https://github.com/pallets/markupsafe/pull/261",
    },
    "httpbin-redirect-to-404": {
        "answer": "The maintainers 404'd the endpoint because of an internal issue.",
        "issue_url": "https://github.com/postmanlabs/httpbin/issues/617",
        "decisive_comment_url": (
            "https://github.com/postmanlabs/httpbin/issues/617#issuecomment-668596347"
        ),
    },
}


@pytest.fixture(scope="module")
def suite():
    return load_domain_suite(DOMAIN, CATALOG_ROOT)


def _score(suite, instance_id: str, **overrides):
    answer = dict(CORRECT_ANSWERS[instance_id])
    answer.update(overrides)
    return score_instance(suite.instance(instance_id), answer)


def test_parse_github_url_reads_every_reference_shape() -> None:
    pull = parse_github_url("https://github.com/encode/httpx/pull/2701/files?diff=split")
    issue = parse_github_url("https://www.github.com/encode/httpx/issues/2694")
    comment = parse_github_url(
        "https://github.com/postmanlabs/httpbin/issues/617#issuecomment-668596347"
    )
    blob = parse_github_url(
        "https://github.com/psf/requests/blob/"
        "0e322af87745eff34caffe4df68456ebc20d9068/src/requests/utils.py#L660-L679"
    )
    release = parse_github_url("https://github.com/pallets/markupsafe/releases/tag/2.1.0")

    assert (pull.kind, pull.repository, pull.number) == ("pull", "encode/httpx", 2701)
    assert (issue.kind, issue.host, issue.number) == ("issue", "github.com", 2694)
    assert comment.comment_id == 668596347
    assert (blob.path, blob.line_start, blob.line_end) == ("src/requests/utils.py", 660, 679)
    assert blob.ref == "0e322af87745eff34caffe4df68456ebc20d9068"
    assert (release.kind, release.tag) == ("release", "2.1.0")
    assert parse_github_url("not a url") is None
    assert parse_github_url(None) is None
    assert parse_github_url("ftp://github.com/encode/httpx/pull/2701") is None


@pytest.mark.parametrize("instance_id", sorted(CORRECT_ANSWERS))
def test_correct_answers_score_one(suite, instance_id: str) -> None:
    result = _score(suite, instance_id)

    assert result.passed, result.failures
    assert result.score == 1.0
    assert result.record()["partial_credit"] is False


def test_wrong_pull_request_number_scores_zero_and_is_flagged_a_near_miss(suite) -> None:
    """A neighbouring pull request number is a different pull request.

    Scoring it as partial credit would let a vendor build a head-to-head lead
    out of answers nobody could act on.
    """
    result = _score(
        suite,
        "httpx-query-bracket-escaping",
        pull_request_url="https://github.com/encode/httpx/pull/2702",
    )

    assert not result.passed
    assert result.score == 0.0
    assert result.failures == ("pull_request_url:number_mismatch:2702",)
    assert [check.near_miss for check in result.checks if not check.passed] == [True]


def test_right_number_in_the_wrong_repository_scores_zero(suite) -> None:
    result = _score(
        suite,
        "httpx-query-bracket-escaping",
        pull_request_url="https://github.com/encode/httpcore/pull/2701",
    )

    assert not result.passed
    assert result.failures == ("pull_request_url:repository_mismatch:encode/httpcore",)
    assert not any(check.near_miss for check in result.checks)


def test_non_github_host_is_refused(suite) -> None:
    result = _score(
        suite,
        "httpx-query-bracket-escaping",
        pull_request_url="https://api.github.com/repos/encode/httpx/pulls/2701",
    )

    assert not result.passed
    assert result.failures == ("pull_request_url:host_mismatch:api.github.com",)


def test_pull_request_cited_through_the_issues_path_is_accepted(suite) -> None:
    """GitHub serves /issues/<n> for a pull request and redirects it."""
    result = _score(
        suite,
        "httpx-query-bracket-escaping",
        pull_request_url="https://github.com/encode/httpx/issues/2701",
    )

    assert result.passed, result.failures


def test_issue_cited_through_the_pull_path_is_refused(suite) -> None:
    """The reverse does not hold: /pull/<n> for a plain issue is a 404."""
    result = _score(
        suite,
        "httpx-query-bracket-escaping",
        issue_url="https://github.com/encode/httpx/pull/2694",
    )

    assert not result.passed
    assert result.failures == ("issue_url:reference_kind_mismatch:pull",)


def test_missing_required_deliverable_key_fails_on_the_key(suite) -> None:
    result = _score(suite, "httpx-query-bracket-escaping", repository="")

    assert not result.passed
    assert "repository:missing_required_key" in result.failures


def test_comment_anchor_is_required_and_must_match(suite) -> None:
    without_anchor = _score(
        suite,
        "httpbin-redirect-to-404",
        decisive_comment_url="https://github.com/postmanlabs/httpbin/issues/617",
    )
    wrong_anchor = _score(
        suite,
        "httpbin-redirect-to-404",
        decisive_comment_url=(
            "https://github.com/postmanlabs/httpbin/issues/617#issuecomment-1089119264"
        ),
    )

    assert without_anchor.failures == ("decisive_comment_url:missing_comment_anchor",)
    assert wrong_anchor.failures == ("decisive_comment_url:comment_mismatch:1089119264",)


def test_repeating_one_call_site_cannot_satisfy_the_minimum(suite) -> None:
    site = CORRECT_ANSWERS["requests-requote-uri-call-sites"]["call_site_urls"][0]

    result = _score(suite, "requests-requote-uri-call-sites", call_site_urls=[site, site])

    assert not result.passed
    assert "call_site_urls:matched_1_of_2" in result.failures


def test_unpinned_source_location_is_refused(suite) -> None:
    result = _score(
        suite,
        "requests-requote-uri-call-sites",
        implementation_url="https://github.com/psf/requests/blob/main/src/requests/utils.py#L660",
    )

    assert not result.passed
    assert result.failures == ("implementation_url:ref_mismatch:main",)


def test_line_anchor_outside_the_recorded_span_is_refused(suite) -> None:
    result = _score(
        suite,
        "requests-requote-uri-call-sites",
        implementation_url=(
            "https://github.com/psf/requests/blob/"
            "0e322af87745eff34caffe4df68456ebc20d9068/src/requests/utils.py#L900"
        ),
    )

    assert not result.passed
    assert result.failures == ("implementation_url:line_out_of_range:900",)


def test_call_site_list_must_be_a_list(suite) -> None:
    result = _score(
        suite,
        "requests-requote-uri-call-sites",
        call_site_urls="https://github.com/psf/requests/blob/main/src/requests/models.py#L480",
    )

    assert not result.passed
    assert "call_site_urls:expected_a_list" in result.failures


def test_release_lineage_requires_the_right_tag_and_change(suite) -> None:
    wrong_tag = _score(
        suite,
        "markupsafe-soft-unicode-removal",
        release="2.0.0",
        release_url="https://github.com/pallets/markupsafe/releases/tag/2.0.0",
    )
    wrong_change = _score(
        suite,
        "markupsafe-soft-unicode-removal",
        change_url="https://github.com/pallets/markupsafe/pull/262",
    )

    assert wrong_tag.failures == (
        "release_url:tag_mismatch:2.0.0",
        "release:value_mismatch:2.0.0",
    )
    assert wrong_change.failures == ("change_url:number_mismatch:262",)


def test_version_fields_require_the_affected_version_and_reject_the_fixed_one(suite) -> None:
    missing = _score(suite, "urllib3-negative-read-amt", affected_versions=["2.1.0"])
    forbidden = _score(suite, "urllib3-negative-read-amt", affected_versions=["2.0.4", "2.2.2"])
    wrong_fix = _score(suite, "urllib3-negative-read-amt", fixed_version="2.2.1")

    assert missing.failures == ("affected_versions:missing:2.0.4",)
    assert forbidden.failures == ("affected_versions:forbidden_present:2.2.2",)
    assert wrong_fix.failures == ("fixed_version:value_mismatch:2.2.1",)


def test_judge_scored_instance_has_no_deterministic_score(suite) -> None:
    with pytest.raises(ValidationError, match="judge-scored"):
        score_instance(suite.instance("pillow-freetypefont-getsize"), {})


def test_match_reference_reports_the_reason_it_refused(suite) -> None:
    reference = suite.instance("httpx-query-bracket-escaping").references_for("issue_url")[0]

    assert match_reference(reference.url, reference) == (True, "ok", False)
    assert match_reference("", reference) == (False, "unparseable_reference", False)


def test_array_keys_are_matched_as_lists_even_with_one_recorded_reference(suite) -> None:
    """Dispatch follows the deliverable schema, not the reference count.

    `evidence_urls` is an array with a single recorded reference. Matching it as
    a bare string would score a correctly-shaped list answer as unparseable.
    """
    from sew.domain_validators import _is_array_key

    instance = suite.instance("pillow-freetypefont-getsize")

    assert _is_array_key(instance, "evidence_urls") is True
    assert (
        _is_array_key(suite.instance("httpx-query-bracket-escaping"), "pull_request_url") is False
    )
    assert _is_array_key(suite.instance("httpx-query-bracket-escaping"), "evidence_urls") is True


def test_a_list_answer_on_a_string_key_is_not_accepted(suite) -> None:
    result = _score(
        suite,
        "httpx-query-bracket-escaping",
        pull_request_url=["https://github.com/encode/httpx/pull/2701"],
    )

    assert not result.passed
    assert result.failures == ("pull_request_url:unparseable_reference",)


def _reference(**overrides):
    """A minimal Reference for exercising match_reference directly."""
    from sew.domain_suites import Reference

    fields = {
        "answer_key": "issue_url",
        "role": "primary",
        "kind": "issue_comment",
        "repository": "encode/httpx",
        "url": "https://github.com/encode/httpx/issues/3103#issuecomment-1234567890",
        "number": 3103,
        "comment_id": 1234567890,
    }
    fields.update(overrides)
    return Reference(**fields)


def test_a_comment_on_a_plain_issue_cannot_be_cited_as_a_pull_request() -> None:
    """GitHub 404s /pull/<n> for a plain issue, so it is a wrong reference.

    The redirect is one-way: /issues/<n> serves a pull request, but /pull/<n>
    does not serve an issue. `issue_comment` took the permissive set
    regardless of its parent, so a hallucinated /pull/ link on an issue
    comment scored a perfect 1.0 for a URL that does not resolve.
    """
    reference = _reference()
    bad = "https://github.com/encode/httpx/pull/3103#issuecomment-1234567890"

    ok, reason, _ = match_reference(bad, reference)

    assert not ok
    assert reason == "reference_kind_mismatch:pull"


def test_a_comment_on_a_pull_request_may_be_cited_either_way() -> None:
    # The permissive direction is still correct when the parent really is a PR.
    reference = _reference(url="https://github.com/encode/httpx/pull/3103#issuecomment-1234567890")

    for form in ("pull", "issues"):
        url = f"https://github.com/encode/httpx/{form}/3103#issuecomment-1234567890"
        ok, reason, _ = match_reference(url, reference)
        assert ok, (form, reason)


def test_a_citation_spanning_far_past_the_cited_range_is_refused() -> None:
    """Starting in the right block is not enough to be a precise citation.

    The end anchor was unchecked, so #L660-L99999 scored a perfect match while
    spanning thousands of irrelevant lines.
    """
    reference = _reference(
        kind="blob",
        url="https://github.com/encode/httpx/blob/abc123/httpx/_urls.py#L660-L670",
        number=None,
        comment_id=None,
        path="httpx/_urls.py",
        commit="abc123",
        line_range=(660, 670),
    )

    too_broad = "https://github.com/encode/httpx/blob/abc123/httpx/_urls.py#L660-L99999"
    ok, reason, _ = match_reference(too_broad, reference)
    assert not ok
    assert reason == "line_span_too_broad:99999"

    within = "https://github.com/encode/httpx/blob/abc123/httpx/_urls.py#L661-L668"
    ok, reason, _ = match_reference(within, reference)
    assert ok, reason


def test_an_uppercase_scheme_is_still_a_parseable_reference() -> None:
    """RFC 3986 schemes are case-insensitive, so HTTPS:// is the same URL.

    The check was byte-exact, so an arm that answered with an uppercase scheme
    scored 0.0 as `unparseable_reference` despite citing the right document.
    """
    parsed = parse_github_url("HTTPS://GitHub.com/encode/httpx/pull/2701")

    assert parsed is not None
    assert parsed.kind == "pull"
    assert parsed.number == 2701

    reference = _reference(
        kind="pull",
        url="https://github.com/encode/httpx/pull/2701",
        number=2701,
        comment_id=None,
    )
    ok, reason, _ = match_reference("HTTPS://github.com/encode/httpx/pull/2701", reference)
    assert ok, reason


def test_a_plural_pulls_parent_url_still_admits_the_pull_citation() -> None:
    """`/pulls/<n>` is a valid spelling of a pull request URL.

    Sniffing the ground-truth URL for the literal "/pull/" read a `/pulls/`
    manifest entry as a plain issue and rejected every correct `/pull/`
    citation as `reference_kind_mismatch:pull`.
    """
    reference = _reference(url="https://github.com/encode/httpx/pulls/3103#issuecomment-1234567890")

    for form in ("pull", "pulls", "issues"):
        url = f"https://github.com/encode/httpx/{form}/3103#issuecomment-1234567890"
        ok, reason, _ = match_reference(url, reference)
        assert ok, (form, reason)


def test_a_boolean_minimum_call_sites_does_not_lower_the_list_floor(suite) -> None:
    """`bool` subclasses `int`, so `minimum_call_sites: true` read as a floor of 1.

    A malformed validator key silently relaxed the call-site list floor from
    "every reference" to "one reference", scoring a half-answered list as
    correct. A non-integer key means unconfigured, so the floor must fall back
    to the full reference count.
    """
    import dataclasses

    instance = suite.instance("requests-requote-uri-call-sites")
    task = dict(instance.task)
    task["deterministic_validator"] = {**instance.validator, "minimum_call_sites": True}
    rigged = dataclasses.replace(instance, task=task)

    answer = dict(CORRECT_ANSWERS["requests-requote-uri-call-sites"])
    answer["call_site_urls"] = answer["call_site_urls"][:1]

    result = score_instance(rigged, answer)

    assert result.score < 1.0
    assert any(
        check.answer_key == "call_site_urls" and not check.passed for check in result.checks
    ), [(check.answer_key, check.passed, check.detail) for check in result.checks]


def test_a_percent_encoded_path_matches_its_decoded_ground_truth() -> None:
    """A vendor that correctly encodes a space cited the right file.

    Comparing `docs/my%20notes.md` to a ground truth of `docs/my notes.md`
    scored an accurate answer path_mismatch -- an index failure it never had.
    """
    from sew.domain_validators import parse_github_url

    parsed = parse_github_url("https://github.com/o/r/blob/abc123/docs/my%20notes.md#L3")

    assert parsed is not None
    assert parsed.path == "docs/my notes.md"
    assert parsed.line_start == 3


def test_an_encoded_slash_stays_inside_its_segment() -> None:
    # Decoding happens per segment, after the split, so `%2F` cannot invent a
    # path boundary the URL never had.
    from sew.domain_validators import parse_github_url

    parsed = parse_github_url("https://github.com/o/r/releases/tag/release%2F2.0")

    assert parsed is not None
    assert parsed.kind == "release"
    assert parsed.tag == "release/2.0"


def test_a_literal_plus_in_a_path_is_not_a_space() -> None:
    from sew.domain_validators import parse_github_url

    parsed = parse_github_url("https://github.com/o/r/blob/main/src/c++/a.cc")

    assert parsed is not None
    assert parsed.path == "src/c++/a.cc"
