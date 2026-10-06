from __future__ import annotations

import json

from sew.catalog import load_domain_tasks
from sew.cli import main
from sew.domain_report import build_domain_report, explain_hypothesis, render_domain_report

DOMAINS = ("code_and_pr", "legal", "entity_resolution", "gtm")
PROVIDERS = ("exa", "parallel-web", "firecrawl")


def _run():
    outcomes = []
    tasks = load_domain_tasks()
    for domain in DOMAINS:
        for provider in PROVIDERS:
            for index, task_id in enumerate(
                t for t, task in tasks.items() if task["domain"] == domain
            ):
                for repetition in range(1, 6):
                    successful = provider == "exa" or (
                        domain == "legal" and provider == "parallel-web"
                    )
                    if domain == "code_and_pr" and index == 0:
                        successful = provider == "firecrawl"
                    outcomes.append(
                        {
                            "domain": domain,
                            "provider_id": provider,
                            "task_id": task_id,
                            "repetition": repetition,
                            "successful": successful,
                            "surface_id": "generic_surface"
                            if provider == "firecrawl" and domain == "legal"
                            else f"{provider}.{domain}",
                            "generic_surface": provider == "firecrawl" and domain == "legal",
                            "evidence_links": [
                                f"bundles/{domain}/{provider}/{task_id}/{repetition}/judge.json"
                            ],
                        }
                    )
    return {"run_id": "fixture-dsb", "outcomes": outcomes}


def test_fixture_scorecard_and_explain_render_all_sections(tmp_path, capsys):
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(_run()), encoding="utf-8")
    assert main(["domains", "report", str(run_path)]) == 0
    report_path = tmp_path / "reports" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    markdown = (tmp_path / "reports" / "report.md").read_text(encoding="utf-8")
    for heading in (
        "Hypothesis verdicts",
        "Head-to-head by domain",
        "Claimed-strength lift",
        "Per-task detail",
    ):
        assert heading in markdown
    assert "REFUTED" in markdown
    assert "generic_surface" in markdown
    assert "bundles/" in markdown
    assert report["hypotheses"][0]["verdict"] == "refuted"
    assert {task["result"] for task in report["hypotheses"][0]["task_details"]} == {
        "won",
        "lost",
    }
    assert main(["domains", "explain", str(report_path), "--hypothesis", "H1"]) == 0
    explained = capsys.readouterr().out
    assert "H1" in explained and "REFUTED" in explained
    assert "H2" not in explained
    assert explain_hypothesis(report, "H1") == render_domain_report(
        {
            **report,
            "hypotheses": [report["hypotheses"][0]],
            "domains": {"code_and_pr": report["domains"]["code_and_pr"]},
        }
    )


def test_domain_explain_reports_missing_or_malformed_file_cleanly(tmp_path, capsys):
    missing = tmp_path / "missing-report.json"
    assert main(["domains", "explain", str(missing), "--hypothesis", "H1"]) == 2
    stderr = capsys.readouterr().err
    assert "sew: error: cannot read domain report" in stderr
    assert "Traceback" not in stderr

    malformed = tmp_path / "report.json"
    malformed.write_text("{", encoding="utf-8")
    assert main(["domains", "explain", str(malformed), "--hypothesis", "H1"]) == 2
    stderr = capsys.readouterr().err
    assert "sew: error: cannot read domain report" in stderr
    assert "Traceback" not in stderr


def test_under_sampled_cell_is_visible_but_cannot_decide_verdict():
    run = _run()
    claimant_rows = [
        row
        for row in run["outcomes"]
        if row["domain"] == "code_and_pr" and row["provider_id"] == "firecrawl"
    ]
    run["outcomes"] = [
        row for row in run["outcomes"] if row not in claimant_rows or row in claimant_rows[:2]
    ]
    report = build_domain_report(run)
    h1 = next(row for row in report["hypotheses"] if row["hypothesis_id"] == "H1")
    assert h1["verdict"] == "not_separable"
    assert h1["interpretation"] == "under_sampled"
    assert next(c for c in report["domains"]["code_and_pr"] if c["provider_id"] == "firecrawl")[
        "under_sampled"
    ]
    assert "under_sampled" in render_domain_report(report)
