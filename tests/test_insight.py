"""INS insight bench: loader, ledger, resume, clustering, strict credit, blinding, scoring, report.

Offline: provider HTTP goes through a fixture transport on the adapters' own
``HttpTransport`` seam, models are stub callables, and pages come from a dict.
"""

from __future__ import annotations

import json
import random
import re
import shutil
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
from sew.insight.run import Ledger, execute_cell, plan_run, read_json, run

SET_ID = "demo-set"
KEYS = {"SEW_EXA_API_KEY": "sk-exa-SECRET", "SEW_TAVILY_API_KEY": "tvly-SECRET",
        "SEW_PARALLEL_WEB_API_KEY": "par-SECRET"}
PAGES = {
    "https://a.example/1": "Intro.\nStripe Minions merge 1300 PRs weekly.\nFooter.",
    "https://b.example/2": "Devin runs in a sandbox VM.",
    "https://c.example/3": "An unrelated page about gardening.",
    "https://d.example/4": "Kiro uses spec files.",
}
WRITTEN = {"objective": "Find how background agents work.", "keyword_queries": ["background agents", "agent sandbox"]}


def _profiles(tmp: Path) -> Path:
    folder = tmp / "profiles"
    folder.mkdir(parents=True)
    shutil.copy(MODULE_ROOT / "config/insight/profiles/_shared.json", folder)
    docs = {
        "exa.search": {"arm": "exa", "operation": "search", "endpoint": "POST https://api.exa.ai/search",
                       "headers": {"x-api-key": "<EXA_API_KEY>", "Exa-Beta": "dyn"},
                       "params": {"query": "{{QUERY}}", "numResults": 10},
                       "freshness_overlay": {"startPublishedDate": "{{AFTER_DATE}}T00:00:00.000Z"},
                       "post_steps": [{"op": "fetch", "endpoint": "POST https://api.exa.ai/contents",
                                       "params": {"urls": "{{TOP_K_URLS}}"}, "top_k": 1}],
                       "est_cost_usd_per_question": 0.01,
                       "unverified_fields": {"contents.highlights.dynamic": {"unverified": True}}},
        "tavily.search": {"arm": "tavily", "operation": "search", "endpoint": "POST https://api.tavily.com/search",
                          "headers": {"Authorization": "Bearer <TAVILY_API_KEY>"},
                          "params": {"query": "{{QUERY}}", "include_usage": True},
                          "est_cost_usd_per_question": 0.016},
        "parallel.research": {"arm": "parallel", "operation": "research",
                              "endpoint": "POST https://api.parallel.ai/v1/tasks/runs",
                              "headers": {"x-api-key": "<PARALLEL_API_KEY>"},
                              "params": {"input": "{{RESEARCH_BRIEF}}", "processor": "pro"},
                              "est_cost_usd_per_question": 0.1,
                              "async": {"poll_endpoint": "GET https://api.parallel.ai/v1/tasks/runs/{run_id}",
                                        "result_endpoint": "GET https://api.parallel.ai/v1/tasks/runs/{run_id}/result",
                                        "interval_s": 1, "timeout_s": 600}},
        "parallel.search": {"arm": "parallel", "operation": "search", "default_arm": False,
                            "endpoint": "POST https://api.parallel.ai/v1/search",
                            "headers": {"x-api-key": "<PARALLEL_API_KEY>"},
                            "params": {"objective": "{{OBJECTIVE}}", "search_queries": "{{KEYWORD_QUERIES}}",
                                       "client_model": "{{EXTRACTOR_MODEL}}"},
                            "post_steps": [{"op": "fetch", "endpoint": "POST https://api.parallel.ai/v1/extract",
                                            "params": {"urls": "{{TOP_K_URLS}}", "objective": "{{OBJECTIVE}}"}}],
                            "est_cost_usd_per_question": 0.005},
    }
    for name, doc in docs.items():
        (folder / f"{name}.json").write_text(json.dumps(doc))
    return folder


def _set(tmp: Path) -> None:
    (tmp / "catalogs" / "insight").mkdir(parents=True)
    doc = {"set_id": SET_ID, "context_for_judges": "The reader runs an agent fleet.",
           "questions": [{"id": "Q1", "text": "How do background coding agents work?"},
                         {"id": "Q2", "text": "What changed recently?", "freshness_days": 30}]}
    (tmp / "catalogs" / "insight" / f"{SET_ID}.json").write_text(json.dumps(doc))


class Transport:
    """Canned vendor responses by URL; records calls; can rate-limit chosen bodies."""

    def __init__(self, fail_query: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.fail_query = fail_query

    def request(self, method, url, *, headers, json_body, timeout_seconds):
        self.calls.append((method, url, dict(headers), json_body))
        if self.fail_query and json_body and self.fail_query in json.dumps(json_body):
            return HttpResponse(429, {"error": "slow down"})
        if url == "https://api.exa.ai/search":
            return HttpResponse(200, {"costDollars": {"total": 0.012}, "debug": "key=sk-exa-SECRET",
                                      "notices": ["site: operator dropped"], "warnings": ["internal"],
                                      "results": [
                {"url": "https://a.example/1", "title": "A", "highlights": ["Stripe Minions merge 1300 PRs weekly."]},
                {"url": "https://b.example/2", "title": "B", "highlights": ["Devin runs in a sandbox VM."]}]})
        if url == "https://api.exa.ai/contents":
            return HttpResponse(200, {"costDollars": {"total": 0.003},
                                      "results": [{"url": u, "text": PAGES[u]} for u in json_body["urls"]]})
        if "tavily" in url:
            return HttpResponse(200, {"usage": {"credits": 2}, "results": [
                {"url": "https://a.example/1", "title": "A", "content": "Stripe's Minions merge 1300 PRs weekly"},
                {"url": "https://c.example/3", "title": "C", "content": "Goose is an open source agent."}]})
        if url.endswith("/v1/search"):
            return HttpResponse(200, {"results": [{"url": "https://b.example/2", "excerpts": ["Devin runs in a sandbox VM."]}]})
        if url.endswith("/v1/extract"):
            return HttpResponse(200, {"results": [{"url": "https://b.example/2", "excerpts": ["short"],
                                                   "full_content": PAGES["https://b.example/2"]}]})
        if url.endswith("/tasks/runs"):
            return HttpResponse(202, {"run_id": "run_1", "status": "queued"})
        if url.endswith("/result"):
            return HttpResponse(200, {"output": {"content": "Kiro uses spec files. See https://d.example/4"}})
        if "/tasks/runs/" in url:
            return HttpResponse(200, {"run_id": "run_1", "status": "completed"})
        raise AssertionError(f"unexpected call {url}")


def _plan(tmp, **kw):
    if not (tmp / "catalogs").exists():
        _set(tmp)
    if not (tmp / "profiles").exists():
        _profiles(tmp)
    return plan_run(SET_ID, set_root=tmp, profile_dir=tmp / "profiles", state_root=tmp / "state",
                    today=date(2026, 11, 11), **kw)


def _run(tmp, transport=None, writer=None, **kw):
    plan = _plan(tmp, **kw)
    return plan, run(plan, transport=transport or Transport(), writer=writer,
                     resolver=CredentialResolver(environ=KEYS), sleep=lambda s: None, progress=None)


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower().replace("'s", "")))


class FakeModel:
    """Deterministic extractor and judge; records every payload it sees."""

    harness_id, model_id = "fake", "fake-1"

    def __init__(self, seen):
        self.seen = seen

    def __call__(self, payload):
        self.seen.append(payload)
        kind = payload["instructions"]
        if kind == G.EXTRACT:
            if payload["answer"]:
                return {"claims": [{"text": "Kiro uses spec files.", "source": 1}]}
            return {"claims": [{"text": s["text"], "source": s["n"]} for s in payload["sources"]]}
        if kind == G.EQUIVALENT:
            return {"same": {p["p"]: int(len(_tokens(p["a"]) & _tokens(p["b"])) >= 5) for p in payload["pairs"]}}
        if kind == G.VERIFY:
            return {"supported": {c["c"]: int(_tokens(c["text"]) <= _tokens(payload["page"]))
                                  for c in payload["claims"]}}
        if kind == G.VALUE:
            return {"value": {f["k"]: {"score": 3 if re.search(r"\d", f["text"]) else 2, "reason": "r"}
                              for f in payload["findings"]}}
        raise AssertionError("unknown step")


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
    assert exa["items"][0]["page_text"] == PAGES["https://a.example/1"] and "page_text" not in exa["items"][1]
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
    paths = write_report(run_dir)
    summary = read_json(Path(paths["summary"]))
    per = summary["per_arm"]
    # Stripe (3) is shared by exa and tavily; Devin (2) is exa's alone; Tavily's Goose claim is
    # unsupported on its own page; Kiro (2) is the research arm's alone. Two questions.
    assert (per["exa.search"]["weighted"], per["exa.search"]["unique_insights"]) == (10, 2)
    assert (per["tavily.search"]["weighted"], per["tavily.search"]["unique_insights"]) == (6, 0)
    assert (per["parallel.research"]["weighted"], per["parallel.research"]["unique_insights"]) == (4, 2)
    assert per["tavily.search"]["unsupported_rate"] == 0.5 and per["exa.search"]["unsupported_rate"] == 0.0
    assert summary["pooled_weighted"] == 14 and per["exa.search"]["share_of_pool"] == pytest.approx(10 / 14, abs=1e-4)
    assert [g["arm"] for g in summary["greedy"]] == ["exa.search", "parallel.research", "tavily.search"]
    assert [g["added_weighted"] for g in summary["greedy"]] == [10, 4, 0]
    assert summary["overlap"]["exa.search"]["tavily.search"] == 2
    assert summary["per_question"]["Q1"]["winners"]["arms"] == ["exa.search"]
    assert per["exa.search"]["cost_per_unique_insight"] == pytest.approx(0.015)
    report = Path(paths["report"]).read_text()
    for heading in ("## Arms", "## If you could only buy k", "## Overlap", "## Per question",
                    "## Is the lead real?", "## Profile fields to confirm"):
        assert heading in report
    assert "n = 2 questions" in report and "`contents.highlights.dynamic`" in report
    history = read_json(Path(paths["history"]))
    assert list(history["runs"]) == [summary["run_id"]]


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
