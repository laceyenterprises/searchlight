from pathlib import Path
import inspect
import json

import pytest

from sew.insight import grade as G
from sew import cli
from sew.judge_transport import HarnessJudgeTransport, JudgeTransportError


@pytest.fixture
def broker_model(insight_helpers):
    class BrokerModel(insight_helpers.model):
        harness_auth = "broker"
        auth_source = None
        broker_provider = None

        def __init__(self, provider="claude-code-corp", model="fake-1"):
            super().__init__([])
            self.claude_broker_provider = provider
            self.model_id = model

        def __call__(self, payload):
            self.auth_source = "broker"
            self.broker_provider = self.claude_broker_provider
            return super().__call__(payload)

    return BrokerModel


@pytest.mark.usefixtures("agent_os_host")
def test_grading_records_completed_transport_auth_and_preserves_resume(tmp_path, insight_helpers, broker_model):
    plan, _ = insight_helpers.run(tmp_path)
    run_dir = Path(plan["run_dir"])
    options = dict(extractor=lambda: broker_model("claude-code"), judge=broker_model,
                   capture=insight_helpers.pages.__getitem__, seed=7, workers=2, set_root=tmp_path, progress=None)
    G.grade_run(run_dir, **options)
    path = run_dir / "grading/grading.json"
    metadata = json.loads(path.read_text())
    assert metadata["extractor"]["broker_provider"] == "claude-code"
    assert metadata["judge"]["broker_provider"] == "claude-code-corp"
    assert metadata["extractor"]["auth_source"] == metadata["judge"]["auth_source"] == "broker"
    summary = G.grade_run(run_dir, **options)
    resumed = json.loads(path.read_text())
    assert summary["model_calls"] == 0
    assert all(resumed[k] == metadata[k] for k in ("extractor", "judge"))
    for changed in (lambda: broker_model("claude-code"), lambda: broker_model(model="fake-2")):
        with pytest.raises(G.InsightError, match="model/auth identity changed"):
            G.grade_run(run_dir, **{**options, "judge": changed})
        assert json.loads(path.read_text()) == resumed


def _record_transports(monkeypatch):
    calls = []
    signature = inspect.signature(HarnessJudgeTransport)
    sentinel = object()

    def transport(*args, **kwargs):
        calls.append(signature.bind(*args, **kwargs).arguments)
        return sentinel

    monkeypatch.setattr(cli, "HarnessJudgeTransport", transport)
    monkeypatch.setenv(cli.LIVE_ENV, "1")
    return calls, sentinel


@pytest.mark.usefixtures("agent_os_host")
def test_insight_cli_forwards_broker_selection(tmp_path, monkeypatch, insight_helpers):
    plan, _ = insight_helpers.run(tmp_path)
    calls, sentinel = _record_transports(monkeypatch)
    monkeypatch.setenv("CLAUDE_BROKER_PROVIDER", "claude-code")
    monkeypatch.setenv("CLAUDE_MODEL_ID", "environment-model")

    def grade(run_dir, *, extractor, judge, **kwargs):
        assert extractor() is sentinel
        assert judge() is sentinel
        return {"errors": []}

    monkeypatch.setattr(G, "grade_run", grade)
    args = cli.build_parser().parse_args([
        "insight", "grade", "--run", plan["run_dir"],
        "--extractor-model", "claude-opus-5-5", "--judge-model", "claude-sonnet-5-5",
        "--harness-auth", "broker", "--claude-broker-provider", "claude-code-corp",
    ])
    assert args.func(args) == 0
    assert [c["model_id"] for c in calls] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert all(c["claude_broker_provider"] == "claude-code-corp" for c in calls)
    assert all(c["harness_auth"] == "broker" for c in calls)


@pytest.mark.usefixtures("agent_os_host")
def test_insight_run_cli_forwards_broker_selection(monkeypatch):
    from sew.insight import run as R

    calls, sentinel = _record_transports(monkeypatch)
    monkeypatch.setattr(R, "plan_run", lambda *a, **kw: {"query_writer_questions": ["Q1"]})

    def run(plan, *, writer):
        assert writer() is sentinel
        return {}

    monkeypatch.setattr(R, "run", run)
    args = cli.build_parser().parse_args([
        "insight", "run", "--set", "demo", "--extractor-model", "claude-opus-5-5",
        "--harness-auth", "broker", "--claude-broker-provider", "claude-code-corp",
    ])
    assert args.func(args) == 0
    assert calls[0]["model_id"] == "claude-opus-5-5"
    assert calls[0]["claude_broker_provider"] == "claude-code-corp"


@pytest.mark.parametrize("flags", [
    ["--harness-auth", "account"],
    ["--harness-auth", "broker", "--judge-harness", "codex"],
    [],
])
def test_provider_flag_rejects_account_or_wrong_harness(tmp_path, monkeypatch, flags, insight_helpers):
    plan, _ = insight_helpers.run(tmp_path)
    monkeypatch.setenv(cli.LIVE_ENV, "1")
    monkeypatch.setenv("SEW_MODE", "standalone")
    if "broker" in flags:
        monkeypatch.setattr(cli.broker_auth, "auth_source", lambda *a: "broker")
    args = cli.build_parser().parse_args([
        "insight", "grade", "--run", plan["run_dir"],
        "--claude-broker-provider", "claude-code-corp", *flags,
    ])
    with pytest.raises(cli.SchemaError, match="requires broker auth and claude-code"):
        args.func(args)


@pytest.mark.usefixtures("agent_os_host")
@pytest.mark.parametrize("flags", [[], ["--judge-model", "judge"], ["--extractor-model", "extractor"]])
def test_broker_grading_requires_both_pinned_models(tmp_path, monkeypatch, flags, insight_helpers):
    plan, _ = insight_helpers.run(tmp_path)
    monkeypatch.setenv(cli.LIVE_ENV, "1")
    args = cli.build_parser().parse_args([
        "insight", "grade", "--run", plan["run_dir"], "--harness-auth", "broker", *flags,
    ])
    with pytest.raises(cli.SchemaError, match="requires pinned"):
        args.func(args)


def test_transport_failure_is_per_job_and_metadata_is_written(tmp_path, insight_helpers):
    plan, _ = insight_helpers.run(tmp_path)

    class Refused(insight_helpers.model):
        def __call__(self, payload):
            raise JudgeTransportError("rate limited")

    run_dir = Path(plan["run_dir"])
    result = G.grade_run(run_dir, extractor=lambda: Refused([]), judge=lambda: insight_helpers.model([]),
                         workers=2, set_root=tmp_path, progress=None)
    assert result["errors"] and all("rate limited" in e for e in result["errors"])
    assert json.loads((run_dir / "grading/grading.json").read_text())["extractor"]["kind"] == "Refused"


def test_unexpected_grading_failure_still_writes_metadata(tmp_path, insight_helpers):
    plan, _ = insight_helpers.run(tmp_path)

    class Broken(insight_helpers.model):
        def __call__(self, payload):
            raise RuntimeError("unexpected")

    run_dir = Path(plan["run_dir"])
    with pytest.raises(RuntimeError, match="unexpected"):
        G.grade_run(run_dir, extractor=lambda: Broken([]), judge=lambda: insight_helpers.model([]),
                    workers=2, set_root=tmp_path, progress=None)
    assert json.loads((run_dir / "grading/grading.json").read_text())["extractor"]["kind"] == "Broken"
