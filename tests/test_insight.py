"""INS insight bench: loader, ledger, resume, clustering, strict credit, blinding, scoring, report.

Offline: provider HTTP goes through a fixture transport on the adapters' own
``HttpTransport`` seam, models are stub callables, and pages come from a dict.
"""

from __future__ import annotations

import json
import random
import re
from datetime import date
from pathlib import Path

import pytest

from conftest import MODULE_ROOT
from sew.cli import main
from sew.judge import BlindingError
from sew.providers import CredentialResolver, HttpResponse, make_provider
from sew.insight import grade as G
from sew.insight.catalog import (
    InsightError, apply_overlay, load_profiles, load_set, load_shared, parse_profile, placeholders,
    render, request_body,
)
from sew.insight.report import bootstrap_ci, holm, sign_test, write_report
from sew.insight.run import Ledger, execute_cell, read_json, run

from conftest import SET_ID, KEYS, PAGES, LONG, WRITTEN, _profiles, _set, Transport, _plan, _run, FakeModel


def _graded(tmp, seed=7):
    plan, _ = _run(tmp)
    seen: list[dict] = []
    summary = G.grade_run(Path(plan["run_dir"]), extractor=lambda: FakeModel(seen), judge=lambda: FakeModel(seen),
                          capture=PAGES.__getitem__, seed=seed, workers=2, set_root=tmp, progress=None)
    return Path(plan["run_dir"]), seen, summary


# -- catalog and profiles --------------------------------------------------


def test_shipped_set_and_profiles_load():
    iset, shared = load_set("software-factories-2026-11"), load_shared()
    assert len(iset.questions) == 24 and iset.context
    profiles = load_profiles()
    runnable = {k for k, p in profiles.items() if p.runnable}
    assert {"exa.search", "exa.research", "parallel.search", "tavily.research", "brave.search",
            "brave.research", "firecrawl.search", "perplexity.research"} <= runnable
    assert not profiles["claude-native.search"].runnable and not profiles["codex-native.search"].runnable
    assert "exa.research.xhigh" not in profiles  # variants are opt-in
    assert set(load_profiles(["exa"])) == {"exa.research", "exa.search"}
    every = load_profiles(["exa", "parallel", "tavily", "brave", "firecrawl", "perplexity", "exa.research.xhigh",
                           "parallel.research.pro", "tavily.research.mini", "exa.search.std"])
    assert every["brave.research"].credential == "brave-answers"
    assert every["exa.search"].headers["Exa-Beta"].startswith("dynamic-highlights")
    assert every["exa.research"].poll["stop"][0] == "POST"
    for profile in every.values():
        for question in (iset.questions[0], iset.questions[-1]):
            values = placeholders(question, profile, shared, date(2026, 11, 11), writer=WRITTEN,
                                  extractor_model="m")
            request_body(profile, question, values)  # raises on any unresolved slot


def test_profile_validation_and_rendering(tmp_path):
    base = {"arm": "exa", "operation": "search", "endpoint": "POST https://api.exa.ai/search",
            "headers": {"x-api-key": "<EXA_API_KEY>"}, "params": {"query": "{{QUERY}}"},
            "est_cost_usd_per_question": 0.01}
    for bad in ({"params": {"query": "fixed"}}, {"kind": "list"}, {"arm": "Acme!"},
                {"est_cost_usd_per_question": -1}, {"async": {"poll_endpoint": "GET https://x/runs"}},
                {"post_steps": [{"op": "fetch", "endpoint": "POST https://x", "params": {}}]}):
        with pytest.raises(InsightError):
            parse_profile({**base, **bad}, "test")
    assert not parse_profile({**base, "endpoint": "claude -p"}, "t").runnable
    assert not parse_profile({**base, "headers": {"x-api-key": "<ANTHROPIC_API_KEY>"}}, "t").runnable
    (tmp_path / "wrong.json").write_text(json.dumps(base))
    with pytest.raises(InsightError, match="file name"):
        load_profiles(directory=tmp_path)
    assert render({"q": ["{{QUERY}}", "{{KW...}}"], "kw": "{{KW}}", "s": "x {{QUERY}}"},
                  {"QUERY": "Q", "KW": ["a", "b"]}) == {"q": ["Q", "a", "b"], "kw": ["a", "b"], "s": "x Q"}
    assert apply_overlay({"messages": [{"content": "brief"}], "a": {"b": 1}},
                         {"messages[0].content_suffix": "Since X.", "a.c": {"d": 2}}) == {
        "messages": [{"content": "brief\n\nSince X."}], "a": {"b": 1, "c": {"d": 2}}}
    _set(tmp_path)
    exa = load_profiles(directory=_profiles(tmp_path / "p"))["exa.search"]
    shared = load_shared(tmp_path / "p" / "profiles")
    fresh = {"id": "Q", "text": "Q?", "freshness_days": 10}
    body = request_body(exa, fresh, placeholders(fresh, exa, shared, date(2026, 11, 11)))
    assert body == {"query": "Q?", "numResults": 10, "startPublishedDate": "2026-11-01T00:00:00.000Z"}


# -- run, spend caps, resume, credentials ----------------------------------


def test_run_records_cells_and_never_writes_a_key(tmp_path):
    transport = Transport()
    plan, summary = _run(tmp_path, transport=transport)
    run_dir = Path(plan["run_dir"])
    assert summary["status_counts"] == {"ok": 6}
    exa = read_json(run_dir / "cells" / "Q1" / "exa.search.json")
    assert exa["cost"] == {"usd": 0.015, "basis": "measured", "credits": 0.0}
    assert exa["request"]["body"] == {"query": "How do background coding agents work?", "numResults": 10}
    assert exa["request"]["headers"] == {"x-api-key": "<EXA_API_KEY>", "Exa-Beta": "dyn"}
    assert [i["url"] for i in exa["items"]] == ["https://a.example/1", "https://b.example/2"]
    assert exa["items"][0]["page_text"] == PAGES["https://a.example/1"] + LONG
    assert "page_text" not in exa["items"][1]
    assert exa["notices"] == ["site: operator dropped"]
    sent = next(c for c in transport.calls if c[1] == "https://api.exa.ai/search")
    assert sent[2] == {"x-api-key": "sk-exa-SECRET", "Exa-Beta": "dyn"}
    assert read_json(run_dir / "cells" / "Q1" / "tavily.search.json")["cost"]["usd"] == pytest.approx(0.016)
    par = read_json(run_dir / "cells" / "Q2" / "parallel.research.json")
    assert par["status"] == "ok" and par["response"]["output"]["content"].startswith("Kiro")
    assert par["cost"]["basis"] == "estimate" and par["request"]["body"]["input"].startswith("Research question:")
    written = "".join(p.read_text() for p in run_dir.rglob("*.json"))
    assert all(secret not in written for secret in KEYS.values())
    assert "<redacted:credential>" in written
    assert read_json(run_dir / "manifest.json")["profiles"]["exa.search"]["unverified"] == [
        "contents.highlights.dynamic"]


def test_query_writer_runs_once_and_feeds_only_its_arms(tmp_path):
    with pytest.raises(InsightError, match="query-writer"):
        _run(tmp_path, arms=["parallel.search"])
    calls: list[dict] = []

    def writer():
        def model(payload):
            calls.append(payload)
            return WRITTEN
        return model

    plan, summary = _run(tmp_path, arms=["parallel.search"], writer=writer, extractor_model="m-1")
    assert summary["status_counts"] == {"ok": 2} and len(calls) == 2
    assert "How do background coding agents work?" in calls[0]["instructions"]
    run_dir = Path(plan["run_dir"])
    assert read_json(run_dir / "query-writer" / "Q1.json")["keyword_queries"] == WRITTEN["keyword_queries"]
    cell = read_json(run_dir / "cells" / "Q1" / "parallel.search.json")
    assert cell["request"]["body"] == {"objective": WRITTEN["objective"],
                                       "search_queries": WRITTEN["keyword_queries"], "client_model": "m-1"}
    assert cell["items"][0]["page_text"] == PAGES["https://b.example/2"]  # full_content, not excerpts
    _run(tmp_path, arms=["parallel.search"], writer=writer)  # resume: no rewrite
    assert len(calls) == 2


def test_resume_skips_complete_cells_and_retries_the_rest(tmp_path):
    plan, first = _run(tmp_path, transport=Transport(fail_query="changed"))
    assert first["status_counts"] == {"ok": 3, "rate_limited": 3}
    again = Transport()
    plan, second = _run(tmp_path, transport=again)
    assert second["status_counts"] == {"ok": 3}
    assert again.calls and not any("background coding" in json.dumps(c[3]) for c in again.calls if c[3])
    assert all(c["done"] for c in _plan(tmp_path)["cells"])
    profile = tmp_path / "profiles" / "exa.search.json"
    profile.write_text(profile.read_text().replace('"numResults": 10', '"numResults": 5'))
    with pytest.raises(InsightError, match="changed since run"):
        run(_plan(tmp_path), transport=Transport(), resolver=CredentialResolver(environ=KEYS), progress=None)


def test_spend_caps(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", max_usd=1.0, arm_caps={"a": 0.3})
    assert ledger.reserve("a", 0.2) and not ledger.reserve("a", 0.2)
    ledger.settle("a", 0.2, 0.25)
    assert not ledger.reserve("a", 0.1) and ledger.reserve("b", 0.7) and not ledger.reserve("b", 0.1)
    assert Ledger(tmp_path / "ledger.json", max_usd=1, arm_caps={}).spent == {"a": 0.25}
    with pytest.raises(InsightError, match="exceeds --max-usd"):
        _run(tmp_path, max_usd=0.05)
    # A measured overrun ($0.015 against a $0.01 estimate) stops the arm at its cap.
    plan, summary = _run(tmp_path, arms=["exa.search"], arm_max_usd=0.02)
    assert summary["status_counts"] == {"ok": 1, "skipped_cap": 1}
    assert summary["spent_usd"] == {"exa.search": 0.015}


def test_missing_credential_is_recorded_not_raised(tmp_path):
    plan = _plan(tmp_path, arms=["exa.search"])
    out = run(plan, transport=Transport(), resolver=CredentialResolver(environ={"SEW_ENV_FILE": "/nonexistent"}),
              progress=None)
    assert out["status_counts"] == {"unavailable": 2} and out["spent_usd_total"] == 0


def test_research_run_stopped_at_its_frozen_timeout_keeps_partial_output(tmp_path):
    profile = parse_profile({
        "arm": "exa", "operation": "research", "endpoint": "POST https://api.exa.ai/agent/runs",
        "headers": {"x-api-key": "<EXA_API_KEY>"}, "params": {"query": "{{RESEARCH_BRIEF}}"},
        "est_cost_usd_per_question": 0.5,
        "async": {"poll_endpoint": "GET https://api.exa.ai/agent/runs/{id}", "interval_s": 10, "timeout_s": 25,
                  "on_timeout": "POST https://api.exa.ai/agent/runs/{id}/stop (keeps results)"}}, "t")
    ticks = iter(range(0, 1000, 10))
    stopped: list[str] = []

    class Slow:
        def request(self, method, url, *, headers, json_body, timeout_seconds):
            if url.endswith("/stop"):
                stopped.append(url)
                return HttpResponse(200, {})
            if method == "POST":
                return HttpResponse(200, {"id": "run_9", "status": "running"})
            done = bool(stopped)
            return HttpResponse(200, {"id": "run_9", "status": "cancelled" if done else "running",
                                      "costDollars": {"total": 0.5},
                                      "output": {"text": "Partial: Kiro uses spec files."} if done else None})

    question = {"id": "Q", "text": "Q?"}
    values = placeholders(question, profile, load_shared(), date(2026, 11, 11))
    record = execute_cell(profile, question, values=values, transport=Slow(),
                          resolver=CredentialResolver(environ=KEYS), sleep=lambda s: None,
                          clock=lambda: next(ticks))
    assert stopped == ["https://api.exa.ai/agent/runs/run_9/stop"]
    assert record["status"] == "ok" and record["stopped_at_timeout"]
    assert record["response"]["output"]["text"].startswith("Partial") and record["cost"]["usd"] == 0.5


def test_exa_adapter_drops_deprecated_type_and_counts_notices():
    units = make_provider("exa")._cost_metadata(
        {"resolvedSearchType": "", "notices": ["n"], "warnings": ["a", "b"], "requestId": "r"})["provider_units"]
    assert units == {"notices_count": 1, "warnings_count": 2, "request_id": "r"}


def test_cli_dry_run_makes_no_calls(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SEW_STATE_ROOT", str(tmp_path))
    assert main(["insight", "run", "--set", "software-factories-2026-11", "--arms", "exa,tavily.search",
                 "--questions", "Q01,Q24", "--dry-run"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["cells_planned"] == 6 and view["fits_cap"] and not (tmp_path / "insight").exists()
    assert "contents.highlights.dynamic" in view["unverified_fields"]["exa.search"]
    monkeypatch.delenv("SEW_HARNESS_LIVE", raising=False)
    assert main(["insight", "run", "--set", "software-factories-2026-11", "--questions", "Q01"]) == 2


# -- grading: clustering, blinding, verification ---------------------------


def test_clustering_merges_duplicates_and_confirmed_pairs():
    claims = [{"id": "x#1", "text": "Stripe Minions merge 1300 PRs weekly."},
              {"id": "y#1", "text": "Stripe Minions merge 1300 PRs weekly"},
              {"id": "y#2", "text": "Stripe's Minions merge 1300 PRs each week, weekly"},
              {"id": "z#1", "text": "Goose is an open source agent."}]
    seen: list[dict] = []
    groups, checked = G.cluster_claims(FakeModel(seen), claims, rng=random.Random(1), arm_ids=["x.search"])
    assert groups == [["x#1", "y#1", "y#2"], ["z#1"]]
    assert checked == 1 and len(seen) == 1  # the exact duplicate needed no model call


def test_blinding_and_seeded_order(tmp_path):
    run_dir, seen, summary = _graded(tmp_path)
    assert summary["model_calls"] == len(seen)
    assert summary["errors"] == []
    extract = [p for p in seen if p["instructions"] == G.EXTRACT]
    assert any(p["vendor_notices"] == ["site: operator dropped"] for p in extract)
    assert not any("internal" in json.dumps(p) for p in extract)  # vendor warnings never reach the model
    judged = [p for p in seen if p["instructions"] != G.EXTRACT]
    assert judged and all(not re.search(r"\b(exa|tavily|parallel)\b", json.dumps(p)) for p in judged)
    values = [p for p in seen if p["instructions"] == G.VALUE]
    assert values and all("http" not in json.dumps(p) for p in values)
    with pytest.raises(BlindingError):
        G.assert_blind({"findings": [{"text": "from exa.search"}]}, ["exa.search"])
    with pytest.raises(BlindingError):
        G.assert_blind({"arm": "x"}, [])
    assert G.redact_brands("Perplexity found it", ["perplexity"], keep="") == "[search vendor] found it"
    assert G.redact_brands("Perplexity found it", ["perplexity"], keep="about Perplexity") == "Perplexity found it"
    _, seen2, _ = _graded(tmp_path / "again")
    order = sorted([f["text"] for f in p["findings"]] for p in values)  # workers interleave questions
    assert order == sorted([f["text"] for f in p["findings"]] for p in seen2 if p["instructions"] == G.VALUE)


def test_regrade_is_resumable(tmp_path):
    run_dir, seen, _ = _graded(tmp_path)
    more: list[dict] = []
    G.grade_run(run_dir, extractor=lambda: FakeModel(more), judge=lambda: FakeModel(more),
                capture=PAGES.__getitem__, set_root=tmp_path, progress=None)
    assert more == []
    with pytest.raises(InsightError, match="seed"):
        G.grade_run(run_dir, extractor=lambda: FakeModel(more), judge=lambda: FakeModel(more), seed=8,
                    set_root=tmp_path, progress=None)


def test_excerpt_keeps_matching_paragraphs():
    page = "\n".join(["filler words here"] * 5000 + ["Minions merge 1300 PRs weekly"])
    cut = G.excerpt(page, ["Minions merge 1300 PRs"], limit=500)
    assert "1300" in cut and len(cut) <= 500


# -- scoring and report ----------------------------------------------------


def test_strict_credit_unique_and_report(tmp_path):
    run_dir, _, _ = _graded(tmp_path)
    paths = write_report(run_dir, set_root=tmp_path)
    assert Path(paths["summary"]).parent == run_dir / "report"
    summary = read_json(Path(paths["summary"]))
    m = {arm: {k: v["value"] for k, v in metrics.items()} for arm, metrics in summary["metrics"].items()}
    # Stripe (3) is shared by exa and tavily; Devin (2) is exa's alone; Tavily's Goose claim is
    # unsupported on its own page; Kiro (2) is the research arm's alone. Two questions.
    assert (m["exa.search"]["weighted"], m["exa.search"]["unique_insights"]) == (10, 2)
    assert (m["tavily.search"]["weighted"], m["tavily.search"]["unique_insights"]) == (6, 0)
    assert (m["parallel.research"]["weighted"], m["parallel.research"]["unique_insights"]) == (4, 2)
    assert m["tavily.search"]["unsupported_rate"] == 0.5 and m["exa.search"]["unsupported_rate"] == 0.0
    assert summary["metrics"]["tavily.search"]["unsupported_rate"]["n"] == 4
    assert summary["metrics"]["exa.search"]["weighted"]["n"] == 2
    lo, hi = summary["metrics"]["exa.search"]["weighted"]["ci95"]
    assert lo <= 10 <= hi
    assert summary["pool"]["weighted"] == 14 and m["exa.search"]["share_of_pool"] == pytest.approx(10 / 14, abs=1e-4)
    assert [g["arm"] for g in summary["greedy"]] == ["exa.search", "parallel.research", "tavily.search"]
    assert [g["added_weighted"] for g in summary["greedy"]] == [10, 4, 0]
    ov = summary["overlap"]
    assert ov["matrix"][ov["arms"].index("exa.search")][ov["arms"].index("tavily.search")] == 2
    q1 = summary["questions"][0]
    assert q1["winners"] == ["exa.search"] and q1["text"] == "How do background coding agents work?"
    stripe = next(c for c in q1["clusters"] if c["value"] == 3)
    assert stripe["credited"] == ["exa.search", "tavily.search"] and not stripe["unique"]
    goose = next(c for c in q1["clusters"] if c["found_by"] == ["tavily.search"])
    assert goose["credited"] == [] and goose["claims"][0]["verdict"] == "unsupported"
    assert m["exa.search"]["cost_per_unique_insight"] == pytest.approx(0.015)
    report = Path(paths["report"]).read_text()
    for heading in ("## Read this first", "## Arms", "## If you could only buy k", "## Overlap",
                    "## Unique insights", "## Per question", "## Is the lead real?", "## Profiles", "## Raw data"):
        assert heading in report
    assert "n = 2 questions" in report and "`contents.highlights.dynamic`" in report
    assert "[cell](../cells/Q1/exa.search.json)" in report
    history = read_json(Path(paths["history"]))
    assert list(history["runs"]) == [summary["run"]["run_id"]]


def test_statistics():
    assert sign_test(5, 0) == pytest.approx(0.0625) and sign_test(0, 0) == 1.0
    assert sign_test(3, 3) == 1.0
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    lo, hi = bootstrap_ci([1, 2, 3, 0], seed=1)
    assert (lo, hi) == bootstrap_ci([1, 2, 3, 0], seed=1) and 0 <= lo <= 6 <= hi <= 12


def test_a_failed_model_step_is_reported_and_retried_later(tmp_path):
    plan, _ = _run(tmp_path)
    broken = lambda: (lambda payload: {"nonsense": True})  # noqa: E731
    summary = G.grade_run(Path(plan["run_dir"]), extractor=broken, judge=broken, capture=PAGES.__getitem__,
                          seed=1, set_root=tmp_path, progress=None)
    assert len(summary["errors"]) == 6 and all(e.split(":")[0].count("/") == 1 for e in summary["errors"])
    assert not (Path(plan["run_dir"]) / "grading" / "extract").exists()


# -- imported arms ---------------------------------------------------------

CAVEAT = "freeform research brief mapped to questions afterwards; not posed each question verbatim"


def _claims_file(tmp, rows):
    path = tmp / "claims.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _snapshot(run_dir):
    return {p.relative_to(run_dir): p.read_bytes() for p in (run_dir / "cells").rglob("*.json")}


def test_import_adds_an_arm_without_touching_other_cells(tmp_path, capsys, monkeypatch):
    import sew.insight.importer as importer

    monkeypatch.setattr(importer, "load_set", lambda set_id, root=None: load_set(set_id, tmp_path))
    plan, _ = _run(tmp_path)
    run_dir = Path(plan["run_dir"])
    before = _snapshot(run_dir)
    claims = _claims_file(tmp_path, [
        {"qid": "Q1", "claim": "Kiro uses spec files.", "source_url": "https://d.example/4", "retrieved_via": "WebFetch"},
        {"qid": "Q1", "claim": "Devin runs in a sandbox VM.", "source_url": "https://b.example/2",
         "retrieved_via": "WebSearch"}])
    assert main(["insight", "import", "--run", run_dir.name, "--state-root", str(tmp_path / "state"),
                 "--arm", "claude-native.agent", "--claims", str(claims), "--label", "Claude Code native search",
                 "--note", CAVEAT]) == 0
    assert json.loads(capsys.readouterr().out)["questions"] == ["Q1"]
    entry = read_json(run_dir / "manifest.json")["profiles"]["claude-native.agent"]
    assert entry["imported"] and entry["label"] == "Claude Code native search" and entry["note"] == CAVEAT
    assert entry["cost_basis"] == "unknown" and len(entry["source_sha256"]) == 64
    cell = read_json(run_dir / "cells" / "Q1" / "claude-native.agent.json")
    assert cell["status"] == "ok" and cell["response"].startswith("Kiro uses spec files.\nSource: https://d.example/4")
    assert not (run_dir / "cells" / "Q2" / "claude-native.agent.json").exists()
    after = _snapshot(run_dir)
    assert {k: v for k, v in after.items() if "claude-native" not in str(k)} == before


def test_import_refusals(tmp_path):
    from sew.insight.importer import import_arm

    plan, _ = _run(tmp_path)
    run_dir = Path(plan["run_dir"])
    ok = [{"qid": "Q1", "claim": "c", "source_url": "https://d.example/4"}]
    common = dict(claims_path=_claims_file(tmp_path, ok), label="x", set_root=tmp_path,
                  profile_dir=tmp_path / "profiles")
    for arm in ("exa.search", "parallel.search", "Bad Arm"):  # in the run, a profile file, malformed
        with pytest.raises(InsightError):
            import_arm(run_dir, arm_id=arm, **common)
    common["claims_path"] = _claims_file(tmp_path, ok + [{"qid": "Q9", "claim": "c", "source_url": "https://x.example"}])
    with pytest.raises(InsightError, match="not in set"):
        import_arm(run_dir, arm_id="claude-native.agent", **common)
    assert not list((run_dir / "cells").glob("*/claude-native.agent.json"))
    assert "claude-native.agent" not in read_json(run_dir / "manifest.json")["profiles"]


def test_imported_arm_is_regraded_and_marked_in_the_report(tmp_path):
    from sew.insight.importer import import_arm

    plan, _ = _run(tmp_path)
    run_dir = Path(plan["run_dir"])
    claims = _claims_file(tmp_path, [{"qid": "Q1", "claim": "Kiro uses spec files.", "source_url": "https://d.example/4"}])
    import_arm(run_dir, arm_id="claude-native.agent", claims_path=claims, label="Claude Code native search",
               note=CAVEAT, usd=1.2, set_root=tmp_path, profile_dir=tmp_path / "profiles")
    seen: list[dict] = []
    G.grade_run(run_dir, extractor=lambda: FakeModel(seen), judge=lambda: FakeModel(seen),
                capture=PAGES.__getitem__, seed=3, set_root=tmp_path, progress=None)
    assert any(p["instructions"] == G.EXTRACT and p["answer"] and "Kiro" in p["answer"] for p in seen)
    summary = read_json(Path(write_report(run_dir, set_root=tmp_path)["summary"]))
    arm = next(a for a in summary["arms"] if a["arm_id"] == "claude-native.agent")
    m = {k: v["value"] for k, v in summary["metrics"]["claude-native.agent"].items()}
    assert arm["imported"] and arm["note"] == CAVEAT and m["insights"] == 1 and m["unique_insights"] == 0
    assert m["cost_usd"] == 1.2 and m["latency_p50_ms"] is None
    assert summary["metrics"]["parallel.research"]["unique_insights"]["value"] == 1  # Q1's Kiro is now shared
    assert summary["imported"][0]["cost_basis"] == "reported"
    assert any(CAVEAT in c for c in summary["caveats"])
    report = (run_dir / "report" / "REPORT.md").read_text()
    assert "| claude-native.agent (imported) |" in report and f"Caveat: {CAVEAT}" in report
    assert '"imported":true' in (run_dir / "report" / "dashboard.html").read_text()
    # Re-import with no Q1 claims replaces only this arm's cells.
    import_arm(run_dir, arm_id="claude-native.agent", label="again", set_root=tmp_path,
               profile_dir=tmp_path / "profiles",
               claims_path=_claims_file(tmp_path, [{"qid": "Q2", "claim": "c", "source_url": "https://d.example/4"}]))
    assert sorted(p.parent.name for p in (run_dir / "cells").glob("*/claude-native.agent.json")) == ["Q2"]


# -- result bundle, dashboard and publication ------------------------------


def _reported(tmp):
    run_dir, _, _ = _graded(tmp)
    write_report(run_dir, set_root=tmp)
    return run_dir


def _links(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "links":
                yield from (x for x in (v.values() if isinstance(v, dict) else v) if x)
            else:
                yield from _links(v)
    elif isinstance(value, list):
        for v in value:
            yield from _links(v)


def test_summary_matches_its_schema_and_every_link_resolves(tmp_path):
    from sew.insight.report import load_schema, validate

    run_dir = _reported(tmp_path)
    summary = read_json(run_dir / "report" / "summary.json")
    assert summary["schema_version"] == 2 and validate(summary, load_schema()) == []
    broken = json.loads(json.dumps(summary))
    del broken["run"]["seed"]
    broken["metrics"]["exa.search"]["weighted"]["n"] = "two"
    assert len(validate(broken, load_schema())) == 2
    links = list(_links(summary))
    assert len(links) > 50 and all((run_dir / "report" / l).resolve().is_file() for l in links)
    claim = summary["questions"][0]["clusters"][0]["claims"][0]
    assert set(claim["links"]) == {"cell", "extract", "verify", "page"} and claim["links"]["page"]


def test_report_bundle_is_stable(tmp_path):
    run_dir = _reported(tmp_path)
    first = {p.name: p.read_bytes() for p in (run_dir / "report").iterdir()}
    write_report(run_dir, set_root=tmp_path)
    assert {p.name: p.read_bytes() for p in (run_dir / "report").iterdir()} == first


def test_dashboard_is_self_contained(tmp_path):
    run_dir = _reported(tmp_path)
    html = (run_dir / "report" / "dashboard.html").read_text()
    data = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    assert json.loads(data.group(1)) == read_json(run_dir / "report" / "summary.json")
    shell = html.replace(data.group(0), "")
    assert not re.search(r"https?://", shell)  # only data links, never fonts, scripts or trackers
    assert not re.search(r"<(link|img|iframe)\b|\bsrc=|@import|fetch\(|XMLHttpRequest", shell)
    for view in ("Leaderboard", "Unique", "Overlap", "Best k", "Cost", "Questions", "Claims", "Files"):
        assert f'label:"{view}"' in shell
    assert "prefers-color-scheme:dark" in shell and 'role="tablist"' in shell and "aria-sort" in shell
    assert "<" not in data.group(1)  # JSON must escape < as \u003c to prevent script breakout.


def test_publish_bundle_is_sanitized_and_passes_the_report_checks(tmp_path):
    import importlib.util
    from sew.insight.publish import publish

    run_dir = _reported(tmp_path)
    root = tmp_path / "repo"
    (root / "reports" / "old").mkdir(parents=True)
    (root / "reports" / "old" / "REPORT.md").write_text("# Old\n")
    (root / "reports" / "README.md").write_text("# Reports\n\n- [Old](old/REPORT.md): a study.\n\nMore text.\n")
    to = root / "reports" / "2026-11-11-demo-insight"
    out = publish(run_dir, to, set_root=tmp_path)
    names = {p.relative_to(to).as_posix() for p in to.rglob("*") if p.is_file()}
    assert {"REPORT.md", "summary.json", "dashboard.html", "methodology.md", "reproduce.md",
            f"set/{SET_ID}.json", "profiles/_shared.json", "profiles/exa.search.json",
            "data/manifest.json", "data/ledger.json", "data/cells/Q1/exa.search.json"} <= names
    assert not any(n.startswith("data/grading/pages") for n in names) and out["files"] == len(names)
    text = "".join(p.read_text() for p in to.rglob("*") if p.is_file())
    assert "lorem ipsum dolor lorem" not in text and "page_text" not in text
    assert "See https://d.example/4" not in text  # the research arm's full answer body stays local
    assert all(secret not in text for secret in KEYS.values()) and "op://" not in text
    cell = read_json(to / "data/cells/Q1/exa.search.json")
    assert cell["response"]["local_only"] and all(len(i["text"]) <= 300 for i in cell["items"])
    summary = read_json(to / "summary.json")
    links = list(_links(summary))
    assert any(l.startswith("local-only:grading/pages/") for l in links)
    assert all((to / l).is_file() for l in links if not l.startswith("local-only:"))
    assert "(local only)" in (to / "REPORT.md").read_text()
    index = (root / "reports" / "README.md").read_text()
    assert index.count("(2026-11-11-demo-insight/REPORT.md)") == 1 and index.index("demo-insight") < index.index("More text")
    publish(run_dir, to, set_root=tmp_path)  # republish replaces the bundle, links once
    assert (root / "reports" / "README.md").read_text().count("demo-insight/REPORT.md") == 1
    spec = importlib.util.spec_from_file_location("check_reports", MODULE_ROOT / "scripts/check_reports.py")
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    assert checks.check(root=root, reports=()) == []  # scrub, links and anchors, as CI runs them


def test_publish_refusals(tmp_path):
    from sew.insight.publish import publish

    run_dir = _reported(tmp_path)
    with pytest.raises(InsightError, match="reports/<yyyy-mm-dd>-<slug>"):
        publish(run_dir, tmp_path / "out" / "demo", set_root=tmp_path)
    other = tmp_path / "reports" / "2026-11-11-taken"
    other.mkdir(parents=True)
    (other / "notes.md").write_text("someone else's study")
    with pytest.raises(InsightError, match="not an insight bundle"):
        publish(run_dir, other, set_root=tmp_path)


def test_sponsor_is_disclosed_and_cannot_tune_other_vendors(tmp_path):
    from sew.insight.catalog import check_sponsor

    _set(tmp_path)
    path = tmp_path / "catalogs" / "insight" / f"{SET_ID}.json"
    doc = json.loads(path.read_text())
    doc["sponsor"] = {"name": "Tavily", "products": ["tavily"], "disclosure": "Tavily paid for the API credits."}
    path.write_text(json.dumps(doc))
    run_dir = _reported(tmp_path)
    summary = read_json(run_dir / "report" / "summary.json")
    assert summary["set"]["sponsor"]["name"] == "Tavily"
    assert [a["arm_id"] for a in summary["arms"] if a["sponsor"]] == ["tavily.search"]
    assert "Sponsor disclosure" in (run_dir / "report" / "REPORT.md").read_text()
    iset = load_set(SET_ID, tmp_path)
    own = parse_profile({"arm": "tavily", "operation": "search", "requested_by": "Tavily",
                         "endpoint": "POST https://api.tavily.com/search", "headers": {"Authorization": "Bearer <TAVILY_API_KEY>"},
                         "params": {"query": "{{QUERY}}"}, "est_cost_usd_per_question": 0.01}, "t")
    check_sponsor(iset, {"tavily.search": own})
    rival = parse_profile({**own.raw, "arm": "exa", "headers": {"x-api-key": "<EXA_API_KEY>"}}, "t")
    with pytest.raises(InsightError, match="own products"):
        check_sponsor(iset, {"exa.search": rival})
    doc["sponsor"] = {"name": "Tavily"}
    path.write_text(json.dumps(doc))
    with pytest.raises(InsightError, match="disclosure"):
        load_set(SET_ID, tmp_path)


@pytest.mark.parametrize("failure", ["timeout", "server"])
def test_async_task_submission_is_not_retried_and_estimate_survives_resume(tmp_path, failure):
    class Ambiguous(Transport):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            if failure == "timeout":
                raise TimeoutError("response lost after task acceptance")
            return HttpResponse(503, {"error": "response lost"})

    transport = Ambiguous()
    plan, _ = _run(tmp_path, transport=transport, arms=["parallel.research"],
                   question_ids=["Q1"], max_usd=0.1, arm_max_usd=0.1)
    assert len(transport.calls) == 1
    run_dir = Path(plan["run_dir"])
    cell = read_json(run_dir / "cells/Q1/parallel.research.json")
    assert cell["status"] != "ok"
    assert cell["cost"]["usd"] == 0.1 and cell["cost"]["basis"] == "estimate"
    assert read_json(run_dir / "ledger.json")["spent"] == {"parallel.research": 0.1}
    _run(tmp_path, transport=transport, arms=["parallel.research"], question_ids=["Q1"],
         max_usd=0.1, arm_max_usd=0.1)
    assert len(transport.calls) == 1  # the cap prevents another ambiguous paid attempt


def test_reproduce_carries_recorded_broker_auth_and_models(tmp_path):
    from sew.insight.publish import _reproduce
    from sew.insight.report import score

    run_dir, _, _ = _graded(tmp_path)
    path = run_dir / "grading/grading.json"
    metadata = read_json(path)
    metadata["judge"].update(auth_source="broker", broker_provider="claude-code-corp")
    path.write_text(json.dumps(metadata))
    text = _reproduce(score(run_dir, set_root=tmp_path), read_json(run_dir / "manifest.json"))
    assert "--harness-auth broker --claude-broker-provider claude-code-corp" in text
    assert "--extractor-model" in text and "--judge-model fake-1" in text


def test_value_accepts_integer_string_scores_and_refuses_others():
    def judge(scores):
        return lambda payload: {"value": {f["k"]: {"score": scores, "reason": "r"} for f in payload["findings"]}}

    findings = {"c1": "Stripe merges 1,300 PRs a week.", "c2": "Agents are useful."}
    got = G.value_findings(judge(" 3"), findings, question="q", context="ctx", baseline=None,
                           rng=random.Random(1), arm_ids=["exa.search"])
    assert {v["score"] for v in got.values()} == {3}
    with pytest.raises(G.InsightError):
        G.value_findings(judge("high"), findings, question="q", context="ctx", baseline=None,
                         rng=random.Random(1), arm_ids=["exa.search"])


def test_claim_support_is_scoped_by_question(tmp_path):
    # Claim ids (<arm>#<n>) repeat across questions; support must not leak between them.
    for qid, url, verdict in (("Q01", "https://a.example/1", 1), ("Q02", "https://a.example/2", 0)):
        (tmp_path / "grading" / "extract" / qid).mkdir(parents=True)
        (tmp_path / "grading" / "extract" / qid / "exa.search.json").write_text(json.dumps(
            {"question_id": qid, "arm_id": "exa.search", "claims": [{"text": f"fact {qid}", "url": url}]}))
        (tmp_path / "grading" / "verify").mkdir(parents=True, exist_ok=True)
        (tmp_path / "grading" / "verify" / f"{qid}.json").write_text(json.dumps(
            {"url": url, "verdicts": {f"fact {qid}": verdict}}))
    claims = G.load_claims(tmp_path)
    assert claims["Q01"][0]["id"] == claims["Q02"][0]["id"] == "exa.search#1"
    assert G.supported_claims(tmp_path, claims) == {("Q01", "exa.search#1"): 1, ("Q02", "exa.search#1"): 0}


def test_answer_urls_are_read_from_decoded_text():
    md = "Minions merge 1,300 PRs a week [1].\n[1] https://stripe.dev/blog/minions\n[2] https://x.example/a."
    _, urls = G._answer_material({"response": {"content": md}})
    assert urls == ["https://stripe.dev/blog/minions", "https://x.example/a"]
    sse = "\n".join("data: " + json.dumps({"choices": [{"delta": {"content": part}}]})
                    for part in ("See https://brave.example/p", "\nand more.")) + "\ndata: [DONE]"
    body, urls = G._answer_material({"response": sse})
    assert urls == ["https://brave.example/p"] and body.startswith("See https://brave.example/p")


def test_arms_run_on_a_subset_are_scored_on_their_questions(tmp_path):
    run_dir, _, _ = _graded(tmp_path)
    (run_dir / "cells" / "Q2" / "parallel.research.json").unlink()  # as if it ran only on Q1
    summary = read_json(Path(write_report(run_dir, set_root=tmp_path)["summary"]))
    m = summary["metrics"]["parallel.research"]
    assert m["weighted"]["n"] == 1 and m["weighted_per_question"]["n"] == 1
    row = next(r for r in summary["arms"] if r["arm_id"] == "parallel.research")
    assert row["counts"]["questions_run"] == 1
    test = next(t for t in summary["paired_tests"] if t["other"] == "parallel.research")
    assert test["n_questions"] == 1
    assert any("ran on a subset" in c for c in summary["caveats"])
    assert not any("did not return ok" in c for c in summary["caveats"])
