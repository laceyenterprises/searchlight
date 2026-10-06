"""DSB claim verification over the complete vendor-by-domain result matrix.

This module deliberately consumes normalized scored outcomes rather than any
one domain runner's private record shape.  DSB-03 through DSB-06 use different
validators, but their experimental unit is the same: one vendor either
completed one domain task successfully or it did not.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .catalog import module_root
from .domain_surfaces import (
    DOMAINS,
    PROVIDERS,
    DomainSurfaceError,
    assert_claim_registry_vocabulary,
    provider_id_for_claimant,
)
from .report import under_sampled
from .retrieval_lane import wilson

VERDICTS = frozenset({"supported", "weak", "refuted", "not_separable"})


class ClaimVerificationError(ValueError):
    """Raised when a claim registry or result matrix cannot be verified."""


def load_claims(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the versioned DSB claim registry."""
    target = path or module_root() / "catalogs" / "domains" / "claims.yaml"
    try:
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ClaimVerificationError(f"cannot load claim registry {target}: {exc}") from exc
    claims = document.get("claims") if isinstance(document, Mapping) else None
    if not isinstance(claims, list) or not claims:
        raise ClaimVerificationError(f"claim registry {target} has no claims")
    return [dict(claim) for claim in claims]


def _validated_claims(claims: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    raw_claims = list(claims)
    try:
        assert_claim_registry_vocabulary(raw_claims)
    except DomainSurfaceError as exc:
        raise ClaimVerificationError(str(exc)) from exc
    validated: list[dict[str, str]] = []
    hypothesis_ids: set[str] = set()
    claimant_domains: set[tuple[str, str]] = set()
    for raw in raw_claims:
        claim: dict[str, str] = {}
        for key in ("hypothesis_id", "claimant", "domain"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ClaimVerificationError(f"claim {key} must be a non-empty string")
            claim[key] = value
        if claim["hypothesis_id"] in hypothesis_ids:
            raise ClaimVerificationError(f"duplicate hypothesis_id {claim['hypothesis_id']}")
        try:
            claim["provider_id"] = provider_id_for_claimant(claim["claimant"])
        except DomainSurfaceError as exc:
            raise ClaimVerificationError(str(exc)) from exc
        claimant_domain = (claim["provider_id"], claim["domain"])
        if claimant_domain in claimant_domains:
            raise ClaimVerificationError(
                f"duplicate claimant/domain pair {claim['provider_id']}/{claim['domain']}"
            )
        hypothesis_ids.add(claim["hypothesis_id"])
        claimant_domains.add(claimant_domain)
        validated.append(claim)
    return validated


def _matrix(
    outcomes: Iterable[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], list[bool]], set[str], set[str]]:
    cells: dict[tuple[str, str], list[bool]] = defaultdict(list)
    domains: set[str] = set()
    provider_ids: set[str] = set()
    for index, row in enumerate(outcomes):
        domain = row.get("domain")
        provider_id = row.get("provider_id")
        successful = row.get("successful")
        if not isinstance(domain, str) or not domain.strip():
            raise ClaimVerificationError(f"outcome {index}.domain must be a non-empty string")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ClaimVerificationError(f"outcome {index}.provider_id must be a non-empty string")
        if not isinstance(successful, bool):
            raise ClaimVerificationError(f"outcome {index}.successful must be boolean")
        cells[(domain, provider_id)].append(successful)
        domains.add(domain)
        provider_ids.add(provider_id)

    if not cells:
        raise ClaimVerificationError("outcomes matrix has no rows")

    missing = sorted(
        f"{domain}/{provider_id}"
        for domain in domains
        for provider_id in provider_ids
        if not cells.get((domain, provider_id))
    )
    if missing:
        raise ClaimVerificationError(
            "full vendor-by-domain matrix is required; missing " + ", ".join(missing)
        )
    return dict(cells), domains, provider_ids


def _cell(domain: str, provider_id: str, outcomes: list[bool]) -> dict[str, Any]:
    successes = sum(outcomes)
    n = len(outcomes)
    return {
        "domain": domain,
        "provider_id": provider_id,
        "successes": successes,
        "n": n,
        "success_rate": successes / n,
        "success_ci_95": list(wilson(successes, n)),
        "under_sampled": under_sampled(n),
    }


def _separability(
    claimant: Mapping[str, Any], competitors: list[Mapping[str, Any]]
) -> tuple[bool, str, list[str]]:
    """Return whether the claimant's win or loss is interval-separable."""
    excluded = sorted(cell["provider_id"] for cell in competitors if cell["under_sampled"])
    competitors = [cell for cell in competitors if not cell["under_sampled"]]
    if claimant["under_sampled"]:
        return False, "under_sampled", excluded
    if not competitors:
        return False, "no_sampled_competitors", excluded
    cells = sorted(
        [claimant, *competitors],
        key=lambda cell: (-cell["success_rate"], cell["provider_id"]),
    )
    leader = cells[0]
    if leader["success_rate"] == claimant["success_rate"] and leader is not claimant:
        return False, "point_estimate_tie", excluded
    if leader is claimant:
        runner_up = cells[1]
        if runner_up["success_rate"] == claimant["success_rate"]:
            return False, "point_estimate_tie", excluded
        if claimant["success_ci_95"][0] <= runner_up["success_ci_95"][1]:
            return False, "intervals_overlap", excluded
        return True, "separable", excluded

    if leader["success_ci_95"][0] <= claimant["success_ci_95"][1]:
        return False, "intervals_overlap", excluded
    return True, "separable", excluded


def verify_claims(
    outcomes: Iterable[Mapping[str, Any]],
    *,
    claims: Iterable[Mapping[str, Any]] | None = None,
    expected_providers: Iterable[str] | None = None,
    expected_domains: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compute head-to-head ranks, own-baseline lift, and claim verdicts.

    ``outcomes`` must contain every expected provider in every expected domain;
    the declared catalogs are the default expectations. Each boolean row is
    one vendor's result for one distinct domain task, never a retry or repeated
    observation of the same task.
    Lift is pooled only from the claimant's rows in the *other* evaluated
    domains; competitor rows can never enter that baseline.
    """
    registry = _validated_claims(claims if claims is not None else load_claims())
    matrix, domains, provider_ids = _matrix(outcomes)

    required_providers = set(PROVIDERS if expected_providers is None else expected_providers)
    required_domains = set(DOMAINS if expected_domains is None else expected_domains)
    unknown_providers = sorted(provider_ids - set(PROVIDERS))
    unknown_domains = sorted(domains - set(DOMAINS))
    if unknown_providers:
        raise ClaimVerificationError(
            "outcomes contain undeclared provider(s): " + ", ".join(unknown_providers)
        )
    if unknown_domains:
        raise ClaimVerificationError(
            "outcomes contain undeclared domain(s): " + ", ".join(unknown_domains)
        )
    missing_expected_providers = sorted(required_providers - provider_ids)
    missing_expected_domains = sorted(required_domains - domains)
    if missing_expected_providers:
        raise ClaimVerificationError(
            "outcomes missing expected provider(s): " + ", ".join(missing_expected_providers)
        )
    if missing_expected_domains:
        raise ClaimVerificationError(
            "outcomes missing expected domain(s): " + ", ".join(missing_expected_domains)
        )

    missing_claim_domains = sorted({claim["domain"] for claim in registry} - domains)
    if missing_claim_domains:
        raise ClaimVerificationError(
            "outcomes missing claimed domain(s): " + ", ".join(missing_claim_domains)
        )
    missing_claimant_providers = sorted(
        f"{claim['hypothesis_id']}:{claim['provider_id']}"
        for claim in registry
        if claim["provider_id"] not in provider_ids
    )
    if missing_claimant_providers:
        raise ClaimVerificationError(
            "outcomes missing claimant provider(s): " + ", ".join(missing_claimant_providers)
        )

    by_domain: dict[str, list[dict[str, Any]]] = {}
    for domain in sorted(domains):
        cells = [
            _cell(domain, provider_id, matrix[(domain, provider_id)])
            for provider_id in sorted(provider_ids)
        ]
        by_domain[domain] = sorted(
            cells, key=lambda cell: (-cell["success_rate"], cell["provider_id"])
        )

    hypotheses: list[dict[str, Any]] = []
    for claim in registry:
        domain = claim["domain"]
        claimant_id = claim["provider_id"]
        domain_cells = by_domain[domain]
        claimant_cell = next(cell for cell in domain_cells if cell["provider_id"] == claimant_id)
        competitors = [cell for cell in domain_cells if cell["provider_id"] != claimant_id]
        separable, separability_reason, excluded_competitors = _separability(
            claimant_cell, competitors
        )

        baseline_domains = [
            other_domain
            for other_domain in sorted(domains - {domain})
            if not next(
                cell for cell in by_domain[other_domain] if cell["provider_id"] == claimant_id
            )["under_sampled"]
        ]
        baseline_outcomes = [
            result
            for other_domain in baseline_domains
            for result in matrix[(other_domain, claimant_id)]
        ]
        baseline_successes = sum(baseline_outcomes)
        baseline_n = len(baseline_outcomes)
        if baseline_n:
            baseline_rate = baseline_successes / baseline_n
            baseline_ci = list(wilson(baseline_successes, baseline_n))
        else:
            baseline_rate = claimant_cell["success_rate"]
            baseline_ci = claimant_cell["success_ci_95"]
        lift = claimant_cell["success_rate"] - baseline_rate
        baseline_under_sampled = under_sampled(baseline_n)
        lift_separable = (
            not baseline_under_sampled and claimant_cell["success_ci_95"][0] > baseline_ci[1]
        )
        excluded_competitor_ids = set(excluded_competitors)
        sampled_competitors = [
            competitor
            for competitor in competitors
            if competitor["provider_id"] not in excluded_competitor_ids
        ]
        claimant_wins = all(
            claimant_cell["success_rate"] > competitor["success_rate"]
            for competitor in sampled_competitors
        )

        if claimant_cell["under_sampled"] or (baseline_under_sampled and bool(domains - {domain})):
            verdict = "not_separable"
            interpretation = "under_sampled"
        elif not separable:
            verdict = "not_separable"
            interpretation = separability_reason
        elif not claimant_wins:
            verdict = "refuted"
            interpretation = "claimant_separably_lost_domain"
        elif lift <= 0:
            verdict = "weak"
            interpretation = "generally_strong_not_specialized"
        elif not lift_separable:
            verdict = "weak"
            interpretation = "positive_lift_not_separable"
        else:
            verdict = "supported"
            interpretation = "separable_domain_win_with_positive_lift"

        hypotheses.append(
            {
                "hypothesis_id": claim["hypothesis_id"],
                "claimant": claim["claimant"],
                "provider_id": claimant_id,
                "domain": domain,
                "head_to_head_rank": 1
                + sum(
                    cell["success_rate"] > claimant_cell["success_rate"] for cell in domain_cells
                ),
                "separable": separable,
                "separability_reason": separability_reason,
                "excluded_under_sampled_competitors": excluded_competitors,
                "in_domain_rate": claimant_cell["success_rate"],
                "own_cross_domain_baseline": {
                    "excluded_domain": domain,
                    "included_domains": baseline_domains,
                    "successes": baseline_successes,
                    "n": baseline_n,
                    "success_rate": baseline_rate,
                    "success_ci_95": baseline_ci,
                    "under_sampled": baseline_under_sampled,
                },
                "lift": lift,
                "lift_classification": (
                    "positive_lift"
                    if lift > 0 and lift_separable
                    else "lift_not_separable"
                    if lift > 0
                    else "no_lift"
                ),
                "verdict": verdict,
                "interpretation": interpretation,
            }
        )

    return {
        "domains": {domain: by_domain[domain] for domain in sorted(by_domain)},
        "hypotheses": hypotheses,
    }


__all__ = [
    "ClaimVerificationError",
    "VERDICTS",
    "load_claims",
    "verify_claims",
]
