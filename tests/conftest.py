from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date
import signal
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
LIB_PYTHON = MODULE_ROOT / "lib" / "python"

if not os.environ.get("SEW_TEST_INSTALLED") and str(LIB_PYTHON) not in sys.path:
    sys.path.insert(0, str(LIB_PYTHON))

CATALOG_ROOT = MODULE_ROOT
FIXTURE_ROOT = MODULE_ROOT / "fixtures" / "runs"


@pytest.fixture(scope="session", autouse=True)
def _session_sigint_handler():
    """Background shells can pass SIG_IGN through even an env -i launch."""
    inherited = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, inherited if inherited is not None else signal.SIG_DFL)


@pytest.fixture(autouse=True)
def _guard_sigint_handler(_session_sigint_handler):
    try:
        yield
    finally:
        handler = signal.getsignal(signal.SIGINT)
        # Restore even when the assertion fails, so the offender cannot also
        # disable subsequent interrupt tests (or the operator's Ctrl-C).
        signal.signal(signal.SIGINT, signal.default_int_handler)
        assert handler is signal.default_int_handler, f"test leaked a SIGINT handler: {handler!r}"


@pytest.fixture(autouse=True)
def _isolated_codex_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _guard_sigint_handler,
):
    """Never read the host's real ~/.codex.

    Codex cells pin the model their source config selects, so a developer
    host's ``~/.codex/config.toml`` (e.g. ``model = "gpt-6-sol"``) would change
    Codex argv, recorded model ids and prices between a laptop and CI. Tests
    that want a pinned model write their own config under this empty home.
    """

    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.mktemp("codex-home")))


@pytest.fixture
def agent_os_host(tmp_path, monkeypatch):
    """Host seam fixture for existing tests that exercise broker-based harnesses."""
    from sew import host

    class FixtureHost:
        mode = "agent-os"

        def state_root(self, app_id="sew"):
            return tmp_path / app_id

        def harness_auth(self, harness):
            pytest.fail("unexpected broker service call")

        def meter_provider_call(self, **call):
            return True

    fixture = FixtureHost()
    monkeypatch.setattr(host, "get_host", lambda env=None: fixture)
    return fixture


@pytest.fixture(scope="session")
def containment_capabilities():
    from containment import probe_containment

    return probe_containment()


@pytest.fixture
def require_containment(containment_capabilities):
    from containment import require_capability

    def require(name=None):
        if name is None:
            name = "seatbelt" if sys.platform == "darwin" else "bubblewrap"
        require_capability(containment_capabilities, name)

    return require


# Shared offline insight fixtures; independent of pytest test-module import mode.
from sew.insight import grade as G
from sew.insight.run import plan_run, run
from sew.providers import CredentialResolver, HttpResponse

SET_ID = "demo-set"
KEYS = {"SEW_EXA_API_KEY": "sk-exa-SECRET", "SEW_TAVILY_API_KEY": "tvly-SECRET",
        "SEW_PARALLEL_WEB_API_KEY": "par-SECRET"}
PAGES = {
    "https://a.example/1": "Intro.\nStripe Minions merge 1300 PRs weekly.\nFooter.",
    "https://b.example/2": "Devin runs in a sandbox VM.",
    "https://c.example/3": "An unrelated page about gardening.",
    "https://d.example/4": "Kiro uses spec files.",
}
LONG = " Appendix: " + "lorem ipsum dolor " * 40  # page text beyond any publishable excerpt
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
                                      "results": [{"url": u, "text": PAGES[u] + LONG} for u in json_body["urls"]]})
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


@pytest.fixture
def insight_helpers():
    from types import SimpleNamespace

    return SimpleNamespace(run=_run, model=FakeModel, pages=PAGES)
