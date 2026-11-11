from pathlib import Path
import json

from test_insight import _run, FakeModel, PAGES, G


def test_grading_records_completed_transport_auth(tmp_path):
    plan, _ = _run(tmp_path)

    class BrokerModel(FakeModel):
        auth_source = None
        broker_provider = None

        def __call__(self, payload):
            self.auth_source = "broker"
            self.broker_provider = "claude-code-corp"
            return super().__call__(payload)

    run_dir = Path(plan["run_dir"])
    G.grade_run(
        run_dir,
        extractor=lambda: BrokerModel([]),
        judge=lambda: BrokerModel([]),
        capture=PAGES.__getitem__,
        seed=7,
        workers=2,
        set_root=tmp_path,
        progress=None,
    )
    metadata = json.loads((run_dir / "grading/grading.json").read_text())
    for kind in ("extractor", "judge"):
        assert metadata[kind]["auth_source"] == "broker"
        assert metadata[kind]["broker_provider"] == "claude-code-corp"


def test_insight_cli_forwards_broker_selection(tmp_path, monkeypatch):
    from sew import cli

    plan, _ = _run(tmp_path)
    calls = []
    monkeypatch.setenv(cli.LIVE_ENV, "1")
    monkeypatch.setattr(
        cli, "HarnessJudgeTransport", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    def grade(run_dir, *, extractor, judge, **kwargs):
        extractor()
        judge()
        return {"errors": []}

    monkeypatch.setattr(G, "grade_run", grade)
    args = cli.build_parser().parse_args(
        [
            "insight",
            "grade",
            "--run",
            plan["run_dir"],
            "--extractor-model",
            "claude-opus-5-5",
            "--judge-model",
            "claude-sonnet-5-5",
            "--harness-auth",
            "broker",
            "--claude-broker-provider",
            "claude-code-corp",
        ]
    )
    assert args.func(args) == 0
    assert [call[0][1] for call in calls] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert all(call[1]["claude_broker_provider"] == "claude-code-corp" for call in calls)
    assert all(call[1]["harness_auth"] == "broker" for call in calls)
