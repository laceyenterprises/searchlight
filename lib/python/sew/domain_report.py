"""Evidence-linked scorecards for DSB's normalized domain outcomes."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .claim_verification import ClaimVerificationError, load_claims, verify_claims
from .report import _table


class DomainReportError(ValueError):
    """A run cannot produce an auditable scorecard."""


def _links(values: list[str]) -> str:
    return ", ".join(f"[evidence {index + 1}]({value})" for index, value in enumerate(values))


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _ci(value: list[float]) -> str:
    return f"{_pct(value[0])}–{_pct(value[1])}"


def build_domain_report(run: Mapping[str, Any]) -> dict[str, Any]:
    """Score one complete run. Each row is one distinct task observation."""
    rows = run.get("outcomes")
    if not isinstance(rows, list) or not rows:
        raise DomainReportError("run.outcomes must be a non-empty list")
    if not isinstance(run.get("run_id"), str) or not run["run_id"]:
        raise DomainReportError("run.run_id is required")
    seen: set[tuple[str, str, str, int]] = set()
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise DomainReportError(f"outcome {index} must be an object")
        for field in ("domain", "provider_id", "task_id", "surface_id"):
            if not isinstance(raw.get(field), str) or not raw[field]:
                raise DomainReportError(f"outcome {index}.{field} is required")
        if not isinstance(raw.get("successful"), bool) or not isinstance(
            raw.get("generic_surface"), bool
        ):
            raise DomainReportError(f"outcome {index} needs boolean successful/generic_surface")
        evidence = raw.get("evidence_links")
        if (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(link, str) or not link for link in evidence)
        ):
            raise DomainReportError(f"outcome {index} needs evidence_links")
        repetition = raw.get("repetition", 1)
        if not isinstance(repetition, int) or repetition < 1:
            raise DomainReportError(f"outcome {index}.repetition must be positive")
        key = (raw["domain"], raw["provider_id"], raw["task_id"], repetition)
        if key in seen:
            raise DomainReportError(f"duplicate task observation: {key}")
        seen.add(key)
        by_cell[(raw["domain"], raw["provider_id"])].append(dict(raw))

    try:
        claims = load_claims()
        scores = verify_claims(
            rows,
            claims=claims,
            expected_providers={row["provider_id"] for row in rows},
            expected_domains={row["domain"] for row in rows},
        )
    except ClaimVerificationError as exc:
        raise DomainReportError(str(exc)) from exc
    domain_cells: dict[str, list[dict[str, Any]]] = {}
    for domain, cells in scores["domains"].items():
        domain_cells[domain] = []
        for cell in cells:
            observations = by_cell[(domain, cell["provider_id"])]
            surfaces = sorted({row["surface_id"] for row in observations})
            domain_cells[domain].append(
                {
                    **cell,
                    "surfaces": surfaces,
                    "generic_surface": any(row["generic_surface"] for row in observations),
                    "evidence_links": sorted(
                        {link for row in observations for link in row["evidence_links"]}
                    ),
                }
            )

    hypotheses = []
    for result in scores["hypotheses"]:
        claim = next(c for c in claims if c["hypothesis_id"] == result["hypothesis_id"])
        claimant_rows = by_cell[(result["domain"], result["provider_id"])]
        baseline_rows = [
            row
            for (domain, provider), observations in by_cell.items()
            if provider == result["provider_id"]
            and domain in result["own_cross_domain_baseline"]["included_domains"]
            for row in observations
        ]
        task_details = []
        for task_id in sorted({row["task_id"] for row in claimant_rows}):
            observations = [row for row in claimant_rows if row["task_id"] == task_id]
            competitors = [
                row
                for (domain, provider), competitor_rows in by_cell.items()
                if domain == result["domain"] and provider != result["provider_id"]
                for row in competitor_rows
                if row["task_id"] == task_id
            ]
            claimant_rate = sum(row["successful"] for row in observations) / len(observations)
            competitor_rates = {
                provider: sum(
                    row["successful"] for row in competitors if row["provider_id"] == provider
                )
                / sum(row["provider_id"] == provider for row in competitors)
                for provider in sorted({row["provider_id"] for row in competitors})
            }
            task_details.append(
                {
                    "task_id": task_id,
                    "claimant_successes": sum(row["successful"] for row in observations),
                    "claimant_n": len(observations),
                    "claimant_rate": claimant_rate,
                    "competitor_rates": competitor_rates,
                    "result": (
                        "won"
                        if competitor_rates
                        and all(claimant_rate > rate for rate in competitor_rates.values())
                        else "lost"
                        if competitor_rates
                        and any(claimant_rate < rate for rate in competitor_rates.values())
                        else "not_separable"
                    ),
                    "evidence_links": sorted(
                        {
                            link
                            for row in [*observations, *competitors]
                            for link in row["evidence_links"]
                        }
                    ),
                }
            )
        hypotheses.append(
            {
                **result,
                "claim": claim["claim"],
                "source_url": claim["source_url"],
                "retrieved_at": claim["retrieved_at"],
                "domain_evidence_links": sorted(
                    {
                        link
                        for (domain, _provider), observations in by_cell.items()
                        if domain == result["domain"]
                        for row in observations
                        for link in row["evidence_links"]
                    }
                ),
                "baseline_evidence_links": sorted(
                    {link for row in baseline_rows for link in row["evidence_links"]}
                ),
                "task_details": task_details,
            }
        )
    non_separable_domains = {h["domain"] for h in hypotheses if not h["separable"]}
    for domain, cells in domain_cells.items():
        for cell in cells:
            cell["status"] = (
                "under_sampled"
                if cell["under_sampled"]
                else "not_separable"
                if domain in non_separable_domains
                else "sampled"
            )
    domain_winners = {}
    for domain, cells in domain_cells.items():
        sampled = sorted(
            (cell for cell in cells if not cell["under_sampled"]),
            key=lambda cell: (-cell["success_rate"], cell["provider_id"]),
        )
        domain_winners[domain] = (
            sampled[0]["provider_id"]
            if len(sampled) > 1
            and sampled[0]["success_rate"] > sampled[1]["success_rate"]
            and sampled[0]["success_ci_95"][0] > sampled[1]["success_ci_95"][1]
            else "not_separable"
        )
    return {
        "schema_version": 1,
        "run_id": run["run_id"],
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "repetitions": max(row.get("repetition", 1) for row in rows),
        "domains": domain_cells,
        "domain_winners": domain_winners,
        "hypotheses": hypotheses,
        "outcomes": rows,
    }


def render_domain_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Domain scorecard — {report['run_id']}",
        "",
        f"Generated: {report['generated_at']} · repetitions: {report['repetitions']}",
        "",
        "## Hypothesis verdicts",
    ]
    lines.append(
        _table(
            ["hypothesis", "claimant", "domain", "verdict", "reason", "evidence"],
            [
                [
                    h["hypothesis_id"],
                    h["claimant"],
                    h["domain"],
                    h["verdict"].upper(),
                    h["interpretation"],
                    _links(h["domain_evidence_links"]),
                ]
                for h in report["hypotheses"]
            ],
        )
    )
    lines.extend(["", "## Head-to-head by domain (95% Wilson CI)"])
    lines.append(
        _table(
            [
                "domain",
                "vendor",
                "success",
                "95% CI",
                "n",
                "winner",
                "status",
                "surface",
                "evidence",
            ],
            [
                [
                    domain,
                    cell["provider_id"],
                    _pct(cell["success_rate"]),
                    _ci(cell["success_ci_95"]),
                    cell["n"],
                    report["domain_winners"][domain],
                    cell["status"],
                    ", ".join(cell["surfaces"])
                    + (" (generic_surface)" if cell["generic_surface"] else ""),
                    _links(cell["evidence_links"]),
                ]
                for domain, cells in report["domains"].items()
                for cell in cells
            ],
        )
    )
    lines.extend(["", "## Claimed-strength lift"])
    lines.append(
        _table(
            [
                "hypothesis",
                "in-domain",
                "own baseline",
                "lift",
                "separability",
                "verdict",
                "evidence",
            ],
            [
                [
                    h["hypothesis_id"],
                    _pct(h["in_domain_rate"]),
                    _pct(h["own_cross_domain_baseline"]["success_rate"]),
                    f"{h['lift'] * 100:+.1f} pts",
                    h["separability_reason"],
                    h["verdict"].upper(),
                    _links(h["domain_evidence_links"] + h["baseline_evidence_links"]),
                ]
                for h in report["hypotheses"]
            ],
        )
    )
    lines.extend(["", "## Per-task detail"])
    for h in report["hypotheses"]:
        lines.extend(
            [
                "",
                f"### {h['hypothesis_id']} — {h['claimant']} / {h['domain']} — {h['verdict'].upper()}",
                "",
                f"Claim: {h['claim']}",
                f"Source: [{h['source_url']}]({h['source_url']}) (retrieved {h['retrieved_at']})",
                f"Interpretation: {h['interpretation']}; head-to-head: {h['separability_reason']}",
            ]
        )
        lines.append(
            _table(
                ["task", "claimant", "competitors", "result", "evidence"],
                [
                    [
                        task["task_id"],
                        f"{task['claimant_successes']}/{task['claimant_n']}",
                        ", ".join(
                            f"{provider}: {_pct(rate)}"
                            for provider, rate in task["competitor_rates"].items()
                        ),
                        task["result"],
                        _links(task["evidence_links"]),
                    ]
                    for task in h["task_details"]
                ],
            )
        )
    return "\n".join(lines) + "\n"


def explain_hypothesis(report: Mapping[str, Any], hypothesis_id: str) -> str:
    match = next((h for h in report["hypotheses"] if h["hypothesis_id"] == hypothesis_id), None)
    if match is None:
        raise DomainReportError(f"unknown hypothesis: {hypothesis_id}")
    focused = {
        **report,
        "hypotheses": [match],
        "domains": {match["domain"]: report["domains"][match["domain"]]},
    }
    return render_domain_report(focused)


def generate_domain_report(run_path: Path, output_dir: Path | None = None) -> tuple[Path, Path]:
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainReportError(f"cannot read domain run {run_path}: {exc}") from exc
    if not isinstance(run, dict) or not isinstance(run.get("outcomes"), list):
        raise DomainReportError("domain run must be an object with outcomes")
    if any(not isinstance(row, dict) for row in run["outcomes"]):
        raise DomainReportError("domain run outcomes must be objects")
    destination = output_dir or run_path.parent / "reports"
    # Input bundle links are relative to run.json; output links must resolve
    # from reports/report.md and reports/report.json.
    for row in run.get("outcomes", []):
        evidence = row.get("evidence_links")
        if not isinstance(evidence, list):
            continue
        row["evidence_links"] = [
            link
            if not isinstance(link, str) or "://" in link or Path(link).is_absolute()
            else os.path.relpath(run_path.parent / link, destination)
            for link in evidence
        ]
    report = build_domain_report(run)
    destination.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = destination / "report.json", destination / "report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_domain_report(report), encoding="utf-8")
    return json_path, markdown_path
