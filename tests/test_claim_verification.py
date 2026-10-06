from __future__ import annotations

import math

import pytest

from sew.claim_verification import ClaimVerificationError, verify_claims

CLAIMS = [
    {"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"},
    {"hypothesis_id": "H2", "claimant": "parallel", "domain": "legal"},
    {"hypothesis_id": "H3", "claimant": "exa", "domain": "entity_resolution"},
    {"hypothesis_id": "H4", "claimant": "exa", "domain": "gtm"},
]
PROVIDERS = ("exa", "parallel-web", "firecrawl")
DOMAINS = ("code_and_pr", "legal", "entity_resolution", "gtm")


def _outcomes(
    successes: dict[tuple[str, str], int],
    *,
    n: int = 20,
    domains: tuple[str, ...] = DOMAINS,
    providers: tuple[str, ...] = PROVIDERS,
):
    rows = []
    for domain in domains:
        for provider_id in providers:
            count = successes[(domain, provider_id)]
            rows.extend(
                {"domain": domain, "provider_id": provider_id, "successful": index < count}
                for index in range(n)
            )
    return rows


def _hypothesis(report, hypothesis_id):
    return next(row for row in report["hypotheses"] if row["hypothesis_id"] == hypothesis_id)


def _verify(outcomes, *, claims=CLAIMS, providers=PROVIDERS, domains=DOMAINS):
    return verify_claims(
        outcomes,
        claims=claims,
        expected_providers=providers,
        expected_domains=domains,
    )


def test_vendor_dominating_every_domain_is_not_reported_as_specialized() -> None:
    successes = {
        (domain, provider): (20 if provider == "exa" else 0)
        for domain in DOMAINS
        for provider in PROVIDERS
    }

    report = _verify(_outcomes(successes))

    assert _hypothesis(report, "H3")["verdict"] == "weak"
    assert _hypothesis(report, "H4")["verdict"] == "weak"
    assert _hypothesis(report, "H1")["verdict"] == "refuted"
    assert _hypothesis(report, "H2")["verdict"] == "refuted"
    assert _hypothesis(report, "H3")["interpretation"] == "generally_strong_not_specialized"
    assert _hypothesis(report, "H4")["lift"] == 0
    assert _hypothesis(report, "H4")["lift_classification"] == "no_lift"
    assert all(row["verdict"] != "supported" for row in report["hypotheses"])


def test_overlapping_intervals_are_not_separable_not_a_claimant_win() -> None:
    successes = {(domain, provider): 10 for domain in DOMAINS for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 12
    successes[("code_and_pr", "exa")] = 11

    result = _hypothesis(_verify(_outcomes(successes)), "H1")

    assert result["head_to_head_rank"] == 1
    assert result["verdict"] == "not_separable"
    assert result["separability_reason"] == "intervals_overlap"


def test_competitor_tie_does_not_mask_decisive_claimant_loss() -> None:
    successes = {(domain, provider): 10 for domain in DOMAINS for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 0
    successes[("code_and_pr", "exa")] = 20
    successes[("code_and_pr", "parallel-web")] = 20

    result = _hypothesis(_verify(_outcomes(successes)), "H1")

    assert result["head_to_head_rank"] == 3
    assert result["separable"] is True
    assert result["verdict"] == "refuted"
    assert result["interpretation"] == "claimant_separably_lost_domain"


def test_lift_uses_claimants_own_other_domains_only() -> None:
    successes = {(domain, provider): 2 for domain in DOMAINS for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 20
    # Firecrawl's own baseline is 6/60. Competitors' baselines are irrelevant,
    # even though making them low would manufacture a much larger comparison.
    successes[("legal", "firecrawl")] = 4
    successes[("entity_resolution", "firecrawl")] = 1
    successes[("gtm", "firecrawl")] = 1

    result = _hypothesis(_verify(_outcomes(successes)), "H1")

    baseline = result["own_cross_domain_baseline"]
    assert baseline["excluded_domain"] == "code_and_pr"
    assert baseline["included_domains"] == ["entity_resolution", "gtm", "legal"]
    assert baseline["successes"] == 6
    assert baseline["n"] == 60
    assert math.isclose(baseline["success_rate"], 0.1)
    assert math.isclose(result["lift"], 0.9)
    assert result["verdict"] == "supported"


def test_single_domain_claim_has_zero_lift_baseline_instead_of_crashing() -> None:
    claims = [{"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"}]
    successes = {("code_and_pr", provider): 0 for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 20

    result = _hypothesis(
        _verify(
            _outcomes(successes, domains=("code_and_pr",)),
            claims=claims,
            domains=("code_and_pr",),
        ),
        "H1",
    )

    baseline = result["own_cross_domain_baseline"]
    assert baseline["included_domains"] == []
    assert baseline["successes"] == 0
    assert baseline["n"] == 0
    assert baseline["success_rate"] == result["in_domain_rate"]
    assert baseline["success_ci_95"][0] > 0.8
    assert baseline["success_ci_95"][1] == 1.0
    assert baseline["under_sampled"] is True
    assert result["lift"] == 0
    assert result["lift_classification"] == "no_lift"
    assert result["verdict"] == "weak"


def test_active_outcome_providers_are_all_competitors() -> None:
    providers = (*PROVIDERS, "brave")
    claims = [{"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"}]
    successes = {("code_and_pr", provider): 0 for provider in providers}
    successes[("code_and_pr", "brave")] = 100

    report = _verify(
        _outcomes(
            successes,
            n=100,
            domains=("code_and_pr",),
            providers=providers,
        ),
        claims=claims,
        providers=providers,
        domains=("code_and_pr",),
    )
    result = _hypothesis(report, "H1")

    assert [cell["provider_id"] for cell in report["domains"]["code_and_pr"]] == [
        "brave",
        "exa",
        "firecrawl",
        "parallel-web",
    ]
    assert result["head_to_head_rank"] == 2
    assert result["separable"] is True
    assert result["verdict"] == "refuted"


def test_unclaimed_outcome_domains_feed_the_claimants_own_baseline() -> None:
    claims = [{"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"}]
    domains = ("code_and_pr", "legal", "gtm")
    successes = {(domain, provider): 0 for domain in domains for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 20
    successes[("legal", "firecrawl")] = 4
    successes[("gtm", "firecrawl")] = 6

    report = _verify(_outcomes(successes, domains=domains), claims=claims, domains=domains)
    result = _hypothesis(report, "H1")

    assert sorted(report["domains"]) == ["code_and_pr", "gtm", "legal"]
    baseline = result["own_cross_domain_baseline"]
    assert baseline["included_domains"] == ["gtm", "legal"]
    assert baseline["successes"] == 10
    assert baseline["n"] == 40
    assert math.isclose(baseline["success_rate"], 0.25)
    assert math.isclose(result["lift"], 0.75)


def test_claimant_only_smoke_run_is_not_separable_without_competitors() -> None:
    claims = [{"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"}]
    successes = {("code_and_pr", "firecrawl"): 20}

    result = _hypothesis(
        _verify(
            _outcomes(
                successes,
                domains=("code_and_pr",),
                providers=("firecrawl",),
            ),
            claims=claims,
            providers=("firecrawl",),
            domains=("code_and_pr",),
        ),
        "H1",
    )

    assert result["head_to_head_rank"] == 1
    assert result["separable"] is False
    assert result["separability_reason"] == "no_sampled_competitors"
    assert result["verdict"] == "not_separable"


def test_multiple_claimants_can_share_a_domain() -> None:
    claims = [
        {"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"},
        {"hypothesis_id": "H5", "claimant": "exa", "domain": "code_and_pr"},
    ]
    successes = {("code_and_pr", provider): 0 for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 20

    report = _verify(
        _outcomes(successes, domains=("code_and_pr",)),
        claims=claims,
        domains=("code_and_pr",),
    )

    assert [row["hypothesis_id"] for row in report["hypotheses"]] == ["H1", "H5"]


def test_under_sampled_cells_use_the_wsb08_gate() -> None:
    successes = {(domain, provider): 1 for domain in DOMAINS for provider in PROVIDERS}

    result = _hypothesis(_verify(_outcomes(successes, n=2)), "H1")

    assert result["verdict"] == "not_separable"
    assert result["separability_reason"] == "under_sampled"


def test_full_vendor_by_domain_matrix_is_required() -> None:
    successes = {(domain, provider): 1 for domain in DOMAINS for provider in PROVIDERS}
    rows = _outcomes(successes, n=3)
    rows = [
        row
        for row in rows
        if not (row["domain"] == "legal" and row["provider_id"] == "parallel-web")
    ]

    with pytest.raises(ClaimVerificationError, match="missing legal/parallel-web"):
        _verify(rows)


def test_positive_lift_with_overlapping_baseline_interval_is_weak() -> None:
    successes = {(domain, provider): 0 for domain in DOMAINS for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 12
    for domain in ("legal", "entity_resolution", "gtm"):
        successes[(domain, "firecrawl")] = 11

    result = _hypothesis(_verify(_outcomes(successes)), "H1")

    assert result["lift"] > 0
    assert result["lift_classification"] == "lift_not_separable"
    assert result["verdict"] == "weak"
    assert result["interpretation"] == "positive_lift_not_separable"


def test_under_sampled_competitor_is_excluded_without_vetoing_verdict() -> None:
    successes = {(domain, provider): 0 for domain in DOMAINS for provider in PROVIDERS}
    successes[("code_and_pr", "firecrawl")] = 20
    rows = _outcomes(successes)
    rows = [
        row
        for row in rows
        if not (row["domain"] == "code_and_pr" and row["provider_id"] == "parallel-web")
    ]
    rows.extend(
        {"domain": "code_and_pr", "provider_id": "parallel-web", "successful": True}
        for _ in range(2)
    )

    result = _hypothesis(_verify(rows), "H1")

    assert result["separable"] is True
    assert result["excluded_under_sampled_competitors"] == ["parallel-web"]
    assert result["verdict"] == "supported"


def test_default_catalog_expectations_reject_wholly_absent_provider() -> None:
    successes = {(domain, provider): 1 for domain in DOMAINS for provider in PROVIDERS}

    with pytest.raises(ClaimVerificationError, match="brave"):
        verify_claims(_outcomes(successes), claims=CLAIMS)


def test_outcomes_reject_undeclared_provider_typo() -> None:
    providers = (*PROVIDERS, "parallel")
    successes = {(domain, provider): 1 for domain in DOMAINS for provider in providers}

    with pytest.raises(ClaimVerificationError, match="undeclared provider.*parallel"):
        _verify(_outcomes(successes, providers=providers), providers=providers)


def test_registry_rejects_undeclared_domain_and_duplicate_claimant_domain() -> None:
    successes = {(domain, provider): 1 for domain in DOMAINS for provider in PROVIDERS}
    duplicate_claims = [
        {"hypothesis_id": "H1", "claimant": "firecrawl", "domain": "code_and_pr"},
        {"hypothesis_id": "H5", "claimant": "firecrawl", "domain": "code_and_pr"},
    ]
    with pytest.raises(ClaimVerificationError, match="duplicate claimant/domain"):
        _verify(_outcomes(successes), claims=duplicate_claims)

    bad_claims = [{"hypothesis_id": "HX", "claimant": "firecrawl", "domain": "typo"}]
    with pytest.raises(ClaimVerificationError, match="undeclared domain"):
        _verify(_outcomes(successes), claims=bad_claims)


def test_hypotheses_preserve_registry_order_past_h9() -> None:
    claims = [
        {"hypothesis_id": "H10", "claimant": "firecrawl", "domain": "code_and_pr"},
        {"hypothesis_id": "H2", "claimant": "parallel", "domain": "legal"},
    ]
    successes = {(domain, provider): 1 for domain in DOMAINS for provider in PROVIDERS}

    report = _verify(_outcomes(successes), claims=claims)

    assert [row["hypothesis_id"] for row in report["hypotheses"]] == ["H10", "H2"]
