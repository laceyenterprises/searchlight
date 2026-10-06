import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

from sew.cli import build_parser, main
from sew.gap.calibrate import (
    CalibrationError,
    admission,
    calibrate,
    calibration_path,
    load_calibration,
    recalibrate,
    render_calibration,
)


@pytest.mark.parametrize("kind", ["gap", "control"])
@pytest.mark.parametrize("floor", range(6))
@pytest.mark.parametrize("ceiling", range(6))
def test_every_threshold_edge(kind, floor, ceiling):
    verdict, reason = admission(
        kind, [True] * floor + [False] * (5 - floor), [True] * ceiling + [False] * (5 - ceiling)
    )
    assert verdict == ((floor <= 1 and ceiling >= 4) if kind == "gap" else floor >= 4)
    assert reason.startswith("admitted" if verdict else "rejected")


@pytest.mark.parametrize("floor,ceiling,expected", [(0, 1, True), (1, 1, False), (0, 0, False)])
def test_custom_thresholds(floor, ceiling, expected):
    assert (
        admission("gap", [bool(floor)], [bool(ceiling)], reps=1, max_floor=0, min_ceiling=1)[0]
        == expected
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(reps=0),
        dict(reps=True),
        dict(max_floor=-1),
        dict(min_ceiling=float("nan")),
        dict(min_ceiling=2),
    ],
)
def test_invalid_policy(kwargs):
    with pytest.raises(CalibrationError):
        admission("gap", [False] * 5, [True] * 5, **kwargs)


def test_incomplete_reps():
    with pytest.raises(CalibrationError, match="configured reps"):
        admission("gap", [False] * 4, [True] * 5)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "module"
    golden = Path(__file__).parent / "fixtures/gap"
    shutil.copytree(golden, root / "catalogs/gap")
    tasks = json.loads((golden / "tasks.json").read_text())[:1]
    (root / "catalogs/gap/tasks.yaml").write_text(
        yaml.safe_dump(
            dict(schema_version=1, catalog_id="gap-test", authoring_policy="synthetic", tasks=tasks)
        )
    )
    configs = []

    def execute(config, output_root):
        configs.append(config)
        run_dir = output_root / config.run_id_override
        (run_dir / "artifacts").mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"status": "succeeded"}))
        (run_dir / "artifacts/spawn-metadata.json").write_text(
            json.dumps({"model_id": config.model_id, "arm_audit": {"contaminated": False}})
        )
        return SimpleNamespace(bundle_dir=run_dir)

    def grader(task, run_dir, root, wheelhouse, judges):
        return {"outcome": "pass" if "-ceiling-" in run_dir.name else "fail"}

    return dict(
        harness="codex",
        model="gpt-test",
        root=root,
        state_root=tmp_path / "state",
        execute=execute,
        grader=grader,
    ), configs


def test_roundtrip_export_and_table(setup, tmp_path):
    kwargs, configs = setup
    record, path = calibrate(**kwargs, export=tmp_path / "review.json")
    assert record == load_calibration(kwargs["state_root"], "codex", "gpt-test")
    assert record == json.loads((tmp_path / "review.json").read_text())
    assert path == kwargs["state_root"] / "gap/calibration/codex@gpt-test.json"
    assert len(configs) == 10
    assert all(
        c.mode == "live"
        and c.workspace_profile
        and not c.native_search_available
        and c.model_id == "gpt-test"
        for c in configs
    )
    assert [c.provider_id for c in configs] == ["floor"] * 5 + ["ceiling"] * 5
    task = record["tasks"][0]
    assert task["verdict"] == "admitted"
    assert task["floor"]["rate"] == 0 and task["ceiling"]["rate"] == 1
    assert task["catalog_hash"] == record["catalog_hash"] and task["date"] == record["date"]
    table = render_calibration(record)
    assert table.splitlines()[0].split() == ["task", "family", "floor", "ceiling", "verdict"]
    assert "0/5" in table and "5/5" in table and "admitted (gap)" in table
    assert "calibration codex@gpt-test" in table
    assert all(
        cell["config_neutralized_network_attempts"] == 0
        for arm in ("floor", "ceiling")
        for cell in task[arm]["cells"]
    )
    assert "Config-neutralized network attempts: 0" in table
    for arm in ("floor", "ceiling"):
        for cell in task[arm]["cells"]:
            del cell["config_neutralized_network_attempts"]
    assert "Config-neutralized network attempts: 0" in render_calibration(record)


@pytest.mark.parametrize(
    "mutation", ["status", "audit", "missing_audit", "model", "boot", "ungraded"]
)
def test_refuses_bad_cell_without_record(setup, mutation, monkeypatch):
    monkeypatch.setattr("sew.gap.calibrate.time.sleep", lambda delay: None)
    kwargs, configs = setup
    original = kwargs["execute"]

    def execute(config, root):
        result = original(config, root)
        if mutation in {"status", "boot"}:
            (result.bundle_dir / "run.json").write_text(
                json.dumps(
                    {"status": "contaminated" if mutation == "status" else "harness_boot_failed"}
                )
            )
        if mutation in {"audit", "missing_audit", "model"}:
            meta = {
                "model_id": "wrong" if mutation == "model" else config.model_id,
                "arm_audit": {}
                if mutation == "missing_audit"
                else {"contaminated": mutation == "audit"},
            }
            (result.bundle_dir / "artifacts/spawn-metadata.json").write_text(json.dumps(meta))
        return result

    kwargs["execute"] = execute
    if mutation == "ungraded":
        kwargs["grader"] = lambda *a: {"outcome": "not_applicable"}
    with pytest.raises(CalibrationError):
        calibrate(**kwargs)
    assert len(configs) == (3 if mutation in {"boot", "status", "audit"} else 1)
    assert not calibration_path(kwargs["state_root"], "codex", "gpt-test").exists()


def test_tree_guard_and_unsafe_model(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(CalibrationError, match="tracked"):
        calibration_path(tmp_path / "state", "codex", "model")
    with pytest.raises(CalibrationError, match="identifier"):
        calibration_path(tmp_path.parent, "codex", "../escape")


def test_cli_defaults():
    args = build_parser().parse_args(["gap", "calibrate", "--harness", "codex", "--model", "m"])
    assert (args.reps, args.max_floor, args.min_ceiling) == (5, 0.2, 0.8)


@pytest.mark.parametrize("command", ["calibrate", "recalibrate"])
@pytest.mark.parametrize("reps", [0, -1])
def test_invalid_reps_refused_before_spawn_or_write(
    setup, tmp_path, monkeypatch, capsys, command, reps
):
    kwargs, configs = setup
    previous_args = {}
    if command == "recalibrate":
        calibrate(**kwargs)
        previous_args["previous_model"] = kwargs["model"]
        kwargs["model"] = "gpt-new"
        configs.clear()
    before = {p: p.read_bytes() for p in kwargs["state_root"].rglob("*") if p.is_file()}
    export = tmp_path / "export.json"
    with pytest.raises(CalibrationError, match="reps must be a positive integer"):
        (calibrate if command == "calibrate" else recalibrate)(
            **kwargs, **previous_args, reps=reps, export=export
        )

    monkeypatch.setattr("sew.gap.calibrate.module_root", lambda: kwargs["root"])
    monkeypatch.setattr("sew.gap.calibrate.default_state_root", lambda: kwargs["state_root"])
    monkeypatch.setattr("sew.gap.calibrate.run_harness", kwargs["execute"])
    args = ["gap", command, "--harness", "codex", "--model", kwargs["model"]]
    if previous_args:
        args.extend(
            [
                "--previous-model",
                previous_args["previous_model"],
                "--state-root",
                str(kwargs["state_root"]),
            ]
        )
    args.extend(["--reps", str(reps), "--export", str(export)])
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.err == "sew: error: reps must be a positive integer\n"
    assert captured.out == ""
    assert not configs and not export.exists()
    assert {p: p.read_bytes() for p in kwargs["state_root"].rglob("*") if p.is_file()} == before


def test_cli_render(monkeypatch, setup, capsys):
    kwargs, _ = setup
    record, path = calibrate(**kwargs)
    monkeypatch.setattr("sew.gap.calibrate.calibrate", lambda **k: (record, path))
    assert main(["gap", "calibrate", "--harness", "codex", "--model", "gpt-test"]) == 0
    assert str(path) in capsys.readouterr().out


def test_default_execution_grader_wiring(setup, monkeypatch):
    kwargs, _ = setup
    kwargs.pop("grader")
    calls = []

    def verify(task, diff, **options):
        calls.append((task, diff, options))
        return {"outcome": "pass" if "-ceiling-" in diff.parent.parent.name else "fail"}

    monkeypatch.setattr("sew.gap.verify.verify", verify)
    record, _ = calibrate(**kwargs)
    assert record["tasks"][0]["verdict"] == "admitted"
    assert len(calls) == 10
    assert all(
        diff.name == "workspace.diff" and options["root"] == kwargs["root"]
        for _, diff, options in calls
    )
    assert all(
        (diff.parent.parent / "evaluations/calibration-outcome.json").is_file()
        for _, diff, _ in calls
    )


def test_catalog_change_refused(setup):
    kwargs, _ = setup
    original = kwargs["grader"]

    def grader(*args):
        catalog = kwargs["root"] / "catalogs/gap/tasks.yaml"
        catalog.write_text(catalog.read_text() + "\n")
        return original(*args)

    kwargs["grader"] = grader
    with pytest.raises(CalibrationError, match="catalog changed"):
        calibrate(**kwargs)
    assert not calibration_path(kwargs["state_root"], "codex", "gpt-test").exists()


def test_unknown_task_before_spawn(setup):
    kwargs, configs = setup
    with pytest.raises(CalibrationError):
        calibrate(**kwargs, task_ids=["unknown"])
    assert not configs


def test_brief_grader_wiring(tmp_path, monkeypatch):
    from sew.gap.calibrate import _grade, _capture_source

    run = tmp_path / "run"
    (run / "artifacts").mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"harness_id": "codex", "provider_id": "floor"}))
    (run / "artifacts/final-answer.json").write_text(json.dumps({"brief": "evidence"}))
    monkeypatch.setattr("sew.gap.brief_grade.load_brief_rubric", lambda *a: {"rubric": "r"})

    def grade(task, rubric, answer, **options):
        assert task["task_type"] == "brief"
        assert rubric == {"rubric": "r"} and answer == {"brief": "evidence"}
        assert options["judges"] == ["first", "second"]
        assert options["capture_source"] is _capture_source
        assert options["arm"].harness_id == "codex"
        return {"outcome": "pass"}

    monkeypatch.setattr("sew.gap.brief_grade.grade_brief", grade)
    assert _grade({"task_type": "brief"}, run, tmp_path, None, ["first", "second"]) == {
        "outcome": "pass"
    }


@pytest.mark.parametrize("data,expected", [(b"snapshot", "snapshot"), (b"x" * 2_000_001, None)])
def test_bounded_source_capture(monkeypatch, data, expected):
    from email.message import Message
    from io import BytesIO
    from sew.gap.calibrate import _capture_source_direct as _capture_source

    def open_source(request, target, timeout):
        assert request.full_url == "https://8.8.8.8" and timeout == 30
        assert request.get_header("User-agent") == "Agent-OS-SEW-GAP/0.1"
        assert str(target.address) == "8.8.8.8"
        response = BytesIO(data)
        response.headers = Message()
        return response

    monkeypatch.setattr("sew.gap.calibrate._open_reachability_request", open_source)
    if expected is None:
        with pytest.raises(ValueError, match="limit"):
            _capture_source("https://8.8.8.8")
    else:
        assert _capture_source("https://8.8.8.8") == expected


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://example.org", "https://user:pass@example.org"]
)
def test_source_url_refusal(url, monkeypatch):
    from sew.gap.calibrate import _capture_source_direct as _capture_source

    monkeypatch.setattr(
        "sew.gap.calibrate._open_reachability_request",
        lambda *a, **k: pytest.fail("must not fetch"),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        _capture_source(url)


def test_verifier_errors_are_not_task_failures(setup):
    kwargs, _ = setup
    kwargs["grader"] = lambda *a: {"outcome": "fail", "errors": ["sandbox unavailable"]}
    with pytest.raises(CalibrationError, match="infrastructure"):
        calibrate(**kwargs)
    assert not calibration_path(kwargs["state_root"], "codex", "gpt-test").exists()


@pytest.mark.parametrize("status", ["harness_boot_failed", "timeout", "failed"])
def test_retry_recovers_cell_and_continues_batch(setup, monkeypatch, status):
    kwargs, configs = setup
    catalog = kwargs["root"] / "catalogs/gap/tasks.yaml"
    document = yaml.safe_load(catalog.read_text())
    document["tasks"].append({**document["tasks"][0], "id": "golden-second"})
    catalog.write_text(yaml.safe_dump(document))
    original = kwargs["execute"]
    graded_dirs = []
    original_grader = kwargs["grader"]
    sleeps = []
    monkeypatch.setattr("sew.gap.calibrate.time.sleep", sleeps.append)

    def execute(config, root):
        result = original(config, root)
        # Fail a middle cell, after earlier cells have already been graded.
        if config.task_id == "golden-api-break" and "-floor-2" in config.run_id_override:
            if not config.run_id_override.endswith("-att3"):
                (result.bundle_dir / "run.json").write_text(json.dumps({"status": status}))
        return result

    def grader(task, run_dir, *args):
        assert json.loads((run_dir / "run.json").read_text())["status"] == "succeeded"
        graded_dirs.append(run_dir)
        return original_grader(task, run_dir, *args)

    kwargs.update(execute=execute, grader=grader)
    record, _ = calibrate(**kwargs)
    assert len(configs) == 22 and len(graded_dirs) == 20
    assert sleeps == [1, 2]
    assert all(task["verdict"] == "admitted" for task in record["tasks"])
    cell = record["tasks"][0]["floor"]["cells"][1]
    assert cell["attempts"] == 3
    assert [Path(d).name for d in cell["attempt_run_dirs"]] == [
        "golden-api-break-floor-2",
        "golden-api-break-floor-2-att2",
        "golden-api-break-floor-2-att3",
    ]
    assert cell["run_dir"] == cell["attempt_run_dirs"][-1]
    assert all(Path(d).is_dir() for d in cell["attempt_run_dirs"])


@pytest.mark.parametrize("status", ["harness_boot_failed", "timeout", "failed"])
def test_retry_exhaustion_is_bounded_without_admission(setup, monkeypatch, status):
    kwargs, configs = setup
    original = kwargs["execute"]
    sleeps = []
    monkeypatch.setattr("sew.gap.calibrate.time.sleep", sleeps.append)

    def execute(config, root):
        result = original(config, root)
        (result.bundle_dir / "run.json").write_text(json.dumps({"status": status}))
        return result

    kwargs["execute"] = execute
    kwargs["grader"] = lambda *a: pytest.fail("incomplete attempt must not be graded")
    with pytest.raises(CalibrationError, match=f"{status} after 3 attempt"):
        calibrate(**kwargs)
    assert len(configs) == 3 and sleeps == [1, 2]
    assert not calibration_path(kwargs["state_root"], "codex", "gpt-test").exists()


@pytest.mark.parametrize("status", ["provider_unavailable", "budget_exhausted", "cancelled"])
def test_nonretryable_status_stops_immediately(setup, monkeypatch, status):
    kwargs, configs = setup
    original = kwargs["execute"]
    monkeypatch.setattr("sew.gap.calibrate.time.sleep", lambda *a: pytest.fail("must not retry"))

    def execute(config, root):
        result = original(config, root)
        (result.bundle_dir / "run.json").write_text(json.dumps({"status": status}))
        return result

    kwargs["execute"] = execute
    with pytest.raises(CalibrationError, match=f"{status} after 1 attempt"):
        calibrate(**kwargs)
    assert len(configs) == 1


@pytest.mark.parametrize("audit", [{"contaminated": True}, {}])
def test_transient_status_never_bypasses_arm_audit(setup, monkeypatch, audit):
    kwargs, configs = setup
    original = kwargs["execute"]
    monkeypatch.setattr("sew.gap.calibrate.time.sleep", lambda *a: pytest.fail("must not retry"))

    def execute(config, root):
        result = original(config, root)
        (result.bundle_dir / "run.json").write_text(json.dumps({"status": "timeout"}))
        (result.bundle_dir / "artifacts/spawn-metadata.json").write_text(
            json.dumps({"model_id": config.model_id, "arm_audit": audit})
        )
        return result

    kwargs["execute"] = execute
    with pytest.raises(CalibrationError):
        calibrate(**kwargs)
    assert len(configs) == (3 if audit.get("contaminated") is True else 1)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100." + "64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
@pytest.mark.parametrize("via_dns", [False, True])
def test_source_capture_rejects_private_addresses(monkeypatch, address, via_dns):
    from sew.gap.calibrate import _capture_source_direct as _capture_source

    monkeypatch.setattr(
        "sew.gap.calibrate._open_reachability_request",
        lambda *a: pytest.fail("unsafe source must not be fetched"),
    )
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (address, 443))])
    host = "example.org" if via_dns else (f"[{address}]" if ":" in address else address)
    with pytest.raises(ValueError, match="public addresses"):
        _capture_source(f"https://{host}")


def test_source_capture_rejects_mixed_dns_answers(monkeypatch):
    from sew.gap.calibrate import _capture_source_direct as _capture_source

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **k: [(0, 0, 0, "", (ip, 443)) for ip in ("8.8.8.8", "10.0.0.1")],
    )
    monkeypatch.setattr(
        "sew.gap.calibrate._open_reachability_request",
        lambda *a: pytest.fail("must not fetch"),
    )
    with pytest.raises(ValueError, match="public addresses"):
        _capture_source("https://example.org")


@pytest.mark.parametrize(
    "data,charset,expected",
    [
        (b"caf\xe9", "iso-8859-1", "café"),
        (b"bad\xff", None, "bad\ufffd"),
        (b"bad\xff", "unknown-charset", "bad\ufffd"),
    ],
)
def test_source_capture_encoding_and_pinned_transport(monkeypatch, data, charset, expected):
    from email.message import Message
    from io import BytesIO
    from urllib.request import ProxyHandler
    from sew import evaluator
    from sew.gap.calibrate import _capture_source_direct as _capture_source

    dns_calls = []

    def resolve(*a, **k):
        dns_calls.append(a)
        assert len(dns_calls) == 1, "connection must not re-resolve the model URL"
        return [(0, 0, 0, "", ("8.8.8.8", 443))]

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://example.org" and timeout == 30
            assert request.get_header("User-agent") == "Agent-OS-SEW-GAP/0.1"
            response = BytesIO(data)
            response.headers = Message()
            if charset:
                response.headers["Content-Type"] = f"text/html; charset={charset}"
            return response

    def build_opener(*handlers):
        assert next(h for h in handlers if isinstance(h, ProxyHandler)).proxies == {}
        redirect = next(h for h in handlers if isinstance(h, evaluator._NoRedirectHandler))
        assert (
            redirect.redirect_request(None, None, 302, "Found", {}, "https://127.0.0.1/private")
            is None
        )
        handler = next(h for h in handlers if isinstance(h, evaluator._PinnedHTTPSHandler))
        connection = handler._connection_factory("example.org", timeout=30)
        destinations, tls_hosts = [], []
        sock = object()

        def connect(address, *a):
            destinations.append(address)
            return sock

        class TLSContext:
            def wrap_socket(self, raw, server_hostname):
                assert raw is sock
                tls_hosts.append(server_hostname)
                return sock

        connection._create_connection = connect
        connection._context = TLSContext()
        connection.connect()
        assert destinations == [("8.8.8.8", 443)]
        assert tls_hosts == ["example.org"]
        return Opener()

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setattr("socket.getaddrinfo", resolve)
    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    assert _capture_source("https://example.org") == expected
    assert len(dns_calls) == 1


@pytest.mark.parametrize("audit_only", [False, True])
def test_contaminated_reference_excluded_and_rep_retried(setup, audit_only):
    kwargs, configs = setup
    original = kwargs["execute"]

    def execute(config, root):
        result = original(config, root)
        metadata = {
            "model_id": config.model_id,
            "arm_audit": {
                "contaminated": len(configs) == 1,
                "denied_network_attempts": 2,
                "config_neutralized_network_attempts": 99 if len(configs) == 1 else 3,
            },
        }
        (result.bundle_dir / "artifacts/spawn-metadata.json").write_text(json.dumps(metadata))
        if len(configs) == 1 and not audit_only:
            (result.bundle_dir / "run.json").write_text(json.dumps({"status": "contaminated"}))
        return result

    kwargs["execute"] = execute
    graded_dirs = []
    original_grader = kwargs["grader"]

    def grader(task, directory, *args):
        graded_dirs.append(directory)
        return original_grader(task, directory, *args)

    kwargs["grader"] = grader
    record, _ = calibrate(**kwargs, reps=1)
    cell = record["tasks"][0]["floor"]["cells"][0]
    assert cell["attempts"] == 2
    assert cell["excluded_contaminated_run_dirs"] == cell["attempt_run_dirs"][:1]
    assert cell["denied_network_attempts"] == 2
    assert cell["config_neutralized_network_attempts"] == 3
    assert cell["attempt_run_dirs"][0] not in list(map(str, graded_dirs))
    assert len(configs) == 3
    assert record["tasks"][0]["verdict"] == "admitted"
    assert "Denied network attempts: 4" in render_calibration(record)
    assert "Config-neutralized network attempts: 6" in render_calibration(record)
    assert record["excluded_contaminated_attempts"] == 1
    assert "Excluded contaminated attempts: 1" in render_calibration(record)


def test_contamination_retry_bound_message(setup):
    kwargs, configs = setup
    original = kwargs["execute"]

    def execute(config, root):
        result = original(config, root)
        (result.bundle_dir / "run.json").write_text(json.dumps({"status": "contaminated"}))
        return result

    kwargs["execute"] = execute
    kwargs["grader"] = lambda *args: pytest.fail("contaminated cell graded")
    with pytest.raises(
        CalibrationError, match="contamination retry bound exceeded.*after 3 attempts"
    ):
        calibrate(**kwargs, reps=1)
    assert len(configs) == 3
    assert not calibration_path(kwargs["state_root"], "codex", "gpt-test").exists()
