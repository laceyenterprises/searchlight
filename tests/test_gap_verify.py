"""Real offline execution acceptance tests; hidden inputs stay verifier-side."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import shutil
import zipfile

import pytest

from sew.gap.verify import verify
from sew.gap.workspace import capture_diff


@pytest.fixture
def job(tmp_path, require_containment):
    require_containment("seatbelt")
    root = tmp_path / "module"
    catalog = root / "catalogs/gap"
    fixture = catalog / "fixture"
    hidden = catalog / "hidden"
    fixture.mkdir(parents=True)
    hidden.mkdir()
    (fixture / "app.py").write_text("from mini import legacy\ndef value(): return legacy()\n")
    (fixture / "requirements.txt").write_text("mini==1.0\n")
    (fixture / "test_visible.py").write_text("def test_visible():\n    assert 1 + 1 == 2\n")
    (hidden / "test_hidden.py").write_text(
        "def test_migration():\n    from app import value\n    assert value() == 2\n"
    )
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    pins = []
    for version, role, func in [("1.0", "old", "legacy"), ("2.0", "new", "modern")]:
        name = f"mini-{version}-py3-none-any.whl"
        path = wheels / name
        info = f"mini-{version}.dist-info"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mini.py", f"def {func}(): return {int(version[0])}\n")
            z.writestr(
                info + "/METADATA", f"Metadata-Version: 2.1\nName: mini\nVersion: {version}\n"
            )
            z.writestr(
                info + "/WHEEL",
                "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            z.writestr(info + "/RECORD", "")
        pins.append(
            dict(
                name="mini",
                version=version,
                role=role,
                url="https://example.org/" + name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    task = dict(
        fixture="fixture",
        hidden="hidden",
        packages=pins,
        task_type="code",
        verifier=dict(
            visible_commands=["python -m pytest test_visible.py"],
            hidden_commands=["python -m pytest hidden"],
            timeout_seconds=10,
        ),
    )
    workspace = tmp_path / "agent"
    shutil.copytree(fixture, workspace)
    (workspace / "requirements.txt").write_text("mini==2.0\n")
    return root, fixture, workspace, wheels, task


def run(job, tmp_path):
    root, fixture, agent, wheels, task = job
    patch = tmp_path / "workspace.diff"
    capture_diff(fixture, agent, patch)
    result = verify(task, patch, root=root, wheelhouse=wheels)
    assert not (agent / "hidden/test_hidden.py").exists()
    assert not (fixture / "hidden").exists()
    return result


def correct(job):
    (job[2] / "app.py").write_text("from mini import modern\ndef value(): return modern()\n")


def test_correct_migration(job, tmp_path):
    correct(job)
    result = run(job, tmp_path)
    assert result["outcome"] == "pass"
    assert result["escaped_defects"] == result["visible_regressions"] == 0
    assert result["hidden"][0]["tests"][0]["status"] == "pass"


def test_open_agent_boundary_denies_concurrent_verifier_copies(job, tmp_path, monkeypatch):
    """A running cell must deny copies created later by a different GAP run."""
    import importlib
    import select
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sew.gap import sandbox, workspace

    verifier = importlib.import_module("sew.gap.verify")
    root, fixture, agent, wheels, task = job
    correct(job)
    patch = tmp_path / "verification-run/workspace.diff"
    patch.parent.mkdir()
    capture_diff(fixture, agent, patch)
    cell = tmp_path / "other-run/cell"
    cwd = cell / "workspace"
    cwd.mkdir(parents=True)
    old = cell / "old.whl"
    old.write_bytes(b"permitted old source")
    backend = sandbox.select_backend()
    profile = workspace.cache_read_profile(wheels, cell)

    def wrap(command):
        return backend.command(command, cell, profile=profile)

    # Admission exercises the original cache and the account-wide copy root.
    workspace.qualify_cache_reads(wrap, wheels, cwd=cwd, env=os.environ, backend=backend)
    workspace.qualify_verifier_reads(wrap, wheels, cwd=cwd, env=os.environ, backend=backend)
    ready, release = threading.Event(), threading.Event()
    copies = []
    tests = verifier._tests

    def hold_verifier(command, cwd, env, *args, **kw):
        if not copies:
            scratch = cwd.parent
            assert scratch.parent == workspace.VERIFIER_ROOT
            copies.extend((scratch / "wheelhouse/mini-2.0-py3-none-any.whl",))
            copies.extend(Path(env["VIRTUAL_ENV"]).rglob("mini.py"))
            assert len(copies) == 2
            assert all(path.is_file() for path in copies)
            ready.set()
            assert release.wait(20), "agent read check did not finish"
        return tests(command, cwd, env, *args, **kw)

    monkeypatch.setattr(verifier, "_tests", hold_verifier)
    code = r"""
import errno, json, subprocess, sys
from pathlib import Path
print('ready', flush=True)
request = json.loads(sys.stdin.readline())
assert Path(request['old']).read_bytes() == b'permitted old source'
denials = []
for path in request['copies']:
    try:
        Path(path).read_bytes()
    except OSError as exc:
        denials.append(exc.errno in {errno.EACCES, errno.EPERM})
    else:
        denials.append(False)
    discovered = subprocess.run(
        [sys.executable, '-I', '-c', request['discovery'], request['root'],
         Path(path).name, request['cell'], 'concurrent'],
        capture_output=True, text=True, timeout=12,
    )
    assert discovered.returncode == 0, discovered.stderr
    denials.append(json.loads(discovered.stdout)['denied'] is True)
print(json.dumps(denials))
"""
    with subprocess.Popen(
        wrap([sys.executable, "-I", "-u", "-c", code]), cwd=cwd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        try:
            assert select.select([process.stdout], [], [], 12)[0], "agent boundary did not start"
            assert process.stdout.readline().strip() == "ready"
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(verify, task, patch, root=root, wheelhouse=wheels)
                try:
                    assert ready.wait(20), "verifier did not prepare installed package copies"
                    stdout, stderr = process.communicate(json.dumps({
                        "old": str(old), "copies": [str(path) for path in copies],
                        "root": str(workspace.VERIFIER_ROOT), "cell": str(cell),
                        "discovery": workspace.CACHE_DISCOVERY_PROBE,
                    }) + "\n", timeout=15)
                    assert process.returncode == 0, stderr
                    assert json.loads(stdout) == [True] * 4
                finally:
                    release.set()
                assert future.result(timeout=30)["outcome"] == "pass"
        finally:
            release.set()
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert all(not path.exists() for path in copies)


def test_naive_bump(job, tmp_path):
    result = run(job, tmp_path)
    assert result["outcome"] == "fail"
    assert result["escaped_defects"] == 1
    assert result["visible_regressions"] == 0
    assert len(result["reruns"]) == 1


@pytest.mark.parametrize("requirements", ["mini==1.0\n", "", None])
def test_grading_installs_target_despite_unchanged_or_missing_requirements(
    job, tmp_path, requirements
):
    correct(job)
    path = job[2] / "requirements.txt"
    if requirements is None:
        path.unlink()
    else:
        path.write_text(requirements)
    result = run(job, tmp_path)
    # The corrected app imports modern, which is available only in the new wheel.
    assert not result["errors"], result["errors"]
    assert result["outcome"] == "pass", result


def test_visible_regression(job, tmp_path):
    correct(job)
    (job[2] / "test_visible.py").write_text("def test_visible(): assert False\n")
    result = run(job, tmp_path)
    assert result["outcome"] == "fail"
    assert result["visible_regressions"] == 1
    assert result["escaped_defects"] == 0


def test_timeout(job, tmp_path):
    correct(job)
    (job[0] / "catalogs/gap/hidden/test_hidden.py").write_text(
        "import time\ndef test_timeout(): time.sleep(20)\n"
    )
    job[4]["verifier"]["timeout_seconds"] = 3
    result = run(job, tmp_path)
    assert result["outcome"] == "fail"
    assert result["hidden"][0]["timed_out"]
    assert result["reruns"][0]["timed_out"]


def test_flake(job, tmp_path):
    correct(job)
    (job[0] / "catalogs/gap/hidden/test_hidden.py").write_text(
        "from pathlib import Path\ndef test_flaky():\n"
        '    p = Path("flake-marker")\n    seen = p.exists()\n    p.touch()\n    assert seen\n'
    )
    result = run(job, tmp_path)
    assert result["outcome"] == "fail"
    assert result["escaped_defects"] == 1
    assert result["reruns"][0]["passed"]
    assert result["flakes"] == ["hidden.test_hidden::test_flaky"]


@pytest.mark.parametrize("bad", ["collision", "requirements", "hash"])
def test_setup_failures(job, tmp_path, bad):
    if bad == "collision":
        (job[2] / "hidden").mkdir()
        (job[2] / "hidden/fake").touch()
    elif bad == "requirements":
        (job[2] / "requirements.txt").write_text("https://example.org/evil.whl\n")
    elif bad == "hash":
        job[4]["packages"][0]["sha256"] = "0" * 64
    result = run(job, tmp_path)
    assert result["outcome"] == "fail"
    assert result["errors"]


@pytest.mark.parametrize("answer", [None, {"passed": True, "outcome": "pass"}])
def test_grading_uses_execution_without_answer(job, tmp_path, monkeypatch, answer):
    import importlib
    from test_grading import _cell, _write
    from sew.grading import grade_run
    from sew.schema import load_document, validate_evaluation_record
    from sew.gap import catalog

    verifier = importlib.import_module("sew.gap.verify")
    monkeypatch.setattr(catalog, "load_gap_tasks", lambda: {"synthetic": job[4]})
    monkeypatch.setattr(verifier, "module_root", lambda: job[0])
    cell = _cell(tmp_path, "synthetic", answer)
    record = load_document(cell / "run.json")
    record["task_source"] = "gap"
    _write(cell / "run.json", record)
    (cell / "artifacts").mkdir(exist_ok=True)
    capture_diff(job[1], job[2], cell / "artifacts/workspace.diff")
    before = load_document(cell / "metrics/metrics.json")
    assert grade_run(cell, wheelhouse=job[3])["outcome"] == "fail"
    evaluation = validate_evaluation_record(load_document(cell / "evaluations/evaluation.json"))
    assert evaluation["judge"]["kind"] == "execution"
    assert evaluation["execution"]["escaped_defects"] == 1
    after = load_document(cell / "metrics/metrics.json")
    for key in before.keys() - {"failure_categories", "source_use", "source_counts"}:
        assert after[key] == before[key]


def test_invalid_patch_fails(job, tmp_path):
    patch = tmp_path / "invalid.diff"
    patch.write_text("not a patch\n")
    result = verify(job[4], patch, root=job[0], wheelhouse=job[3])
    assert result["outcome"] == "fail"
    assert "GAP patch application failed:" in result["errors"][0]["message"]
    assert "No valid patches" in result["errors"][0]["message"]


def test_rerun_selects_only_failures(job, tmp_path):
    correct(job)
    (job[0] / "catalogs/gap/hidden/test_hidden.py").write_text(
        "def test_pass(): assert True\ndef test_fail(): assert False\n"
    )
    result = run(job, tmp_path)
    assert len(result["hidden"][0]["tests"]) == 2
    assert [t["id"] for t in result["reruns"][0]["tests"]] == ["hidden.test_hidden::test_fail"]


@pytest.mark.parametrize(
    "xml,code",
    [
        ("", 0),
        ("<invalid", 0),
        ('<testsuite><testcase name="skip"><skipped/></testcase></testsuite>', 0),
        ('<testsuite><testcase name="pass"/></testsuite>', 1),
        ("<testsuite/>", 0),
    ],
)
def test_missing_skipped_empty_or_nonzero_reports_fail(tmp_path, monkeypatch, xml, code):
    import importlib

    verifier = importlib.import_module("sew.gap.verify")
    report = tmp_path / "report.xml"

    def execute(*args, **kwargs):
        report.write_text(xml)
        return dict(exit_code=code, timed_out=False, output="")

    monkeypatch.setattr(verifier, "_execute", execute)
    result = verifier._tests("python -m pytest tests", tmp_path, {}, 1, report)
    assert not result["passed"]


def test_grade_cli_accepts_offline_wheelhouse(tmp_path):
    from sew.cli import build_parser

    args = build_parser().parse_args(
        ["bakeoff", "grade", str(tmp_path), "--wheelhouse", str(tmp_path)]
    )
    assert args.wheelhouse == tmp_path


@pytest.mark.parametrize(
    "key,value",
    [("escaped_defects", -1), ("visible_regressions", True), ("hidden", {}), ("outcome", "pass")],
)
def test_execution_schema_refuses_bad_projection(tmp_path, key, value):
    from test_grading import _cell
    from sew.schema import SchemaError, load_document, validate_evaluation_record

    cell = _cell(tmp_path, "synthetic", None)
    record = load_document(cell / "evaluations/evaluation.json")
    record["outcome"] = "fail"
    record["execution"] = dict(
        outcome="fail",
        escaped_defects=0,
        visible_regressions=0,
        visible=[],
        hidden=[],
        reruns=[],
        flakes=[],
        errors=[],
    )
    record["execution"][key] = value
    with pytest.raises(SchemaError):
        validate_evaluation_record(record)


@pytest.mark.parametrize("mode", ["clean", "failure", "timeout"])
def test_execute_reaps_background_child(tmp_path, mode, require_containment):
    require_containment("ps")
    from sew.gap.verify import _execute

    pidfile = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time; from pathlib import Path; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"Path({str(pidfile)!r}).write_text(str(p.pid)); "
        + {"clean": "sys.exit(0)", "failure": "sys.exit(1)", "timeout": "time.sleep(60)"}[mode]
    )
    result = _execute([sys.executable, "-I", "-c", script], tmp_path, {}, 1)
    pid = int(pidfile.read_text())
    try:
        # A killed orphan may remain a zombie briefly while init reaps it.
        deadline = time.monotonic() + 2
        while True:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
            ).stdout.strip()
            if not state or state.startswith("Z") or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert not state or state.startswith("Z"), f"child {pid} still running: {state}"
        assert result["timed_out"] == (mode == "timeout")
        assert result["exit_code"] == {"clean": 0, "failure": 1, "timeout": -signal.SIGKILL}[mode]
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_missing_diff_is_ungraded_deliverable_missing(tmp_path, monkeypatch):
    from test_grading import _cell, _write
    from sew.gap import catalog
    from sew.grading import grade_run
    from sew.schema import load_document

    monkeypatch.setattr(catalog, "load_gap_tasks", lambda: {"synthetic": {"task_type": "code"}})
    cell = _cell(tmp_path, "synthetic", None)
    run = load_document(cell / "run.json")
    run["task_source"] = "gap"
    _write(cell / "run.json", run)
    assert grade_run(cell)["outcome"] == "not_applicable"
    evaluation = load_document(cell / "evaluations/evaluation.json")
    assert evaluation["failure_reasons"] == ["deliverable_missing"]
    assert "execution" not in evaluation
    metrics = load_document(cell / "metrics/metrics.json")
    assert "deliverable_missing" in metrics["failure_categories"]
    assert "execution_failed" not in metrics["failure_categories"]


def test_tests_refuse_unavailable_sandbox(tmp_path, monkeypatch):
    import importlib
    from sew.schema import SchemaError

    verifier = importlib.import_module("sew.gap.verify")
    monkeypatch.setattr(verifier.sys, "platform", "unsupported")

    def unsafe_spawn(*args, **kwargs):
        pytest.fail("must refuse before spawning uncontained code")

    monkeypatch.setattr(verifier.subprocess, "Popen", unsafe_spawn)
    with pytest.raises(SchemaError, match="unsupported on unsupported"):
        verifier._tests("python -m pytest tests", tmp_path, {}, 1, tmp_path / "result.xml")


@pytest.mark.parametrize("missing", ["py", "pygments"])
def test_optional_pytest_tooling(job, tmp_path, monkeypatch, missing):
    import importlib.util

    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == missing else original(name)
    )
    correct(job)
    assert run(job, tmp_path)["outcome"] == "pass"


def test_patched_code_is_contained_including_children(job, tmp_path):
    correct(job)
    sentinel = tmp_path / "operator-secret"
    sentinel.write_text("do not expose")
    outside = tmp_path / "outside-write"
    (job[2] / "test_visible.py").write_text(
        "import errno, socket, subprocess, sys\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "def test_boundary():\n"
        f"    secret = Path({str(sentinel)!r})\n"
        f"    outside = Path({str(outside)!r})\n"
        "    for operation in [secret.read_text, lambda: outside.write_text('escaped')]:\n"
        "        with pytest.raises(PermissionError): operation()\n"
        "    link = Path('secret-link'); link.symlink_to(secret)\n"
        "    with pytest.raises(PermissionError): link.read_text()\n"
        "    with socket.socket() as s:\n"
        "        with pytest.raises(PermissionError): s.connect(('127.0.0.1', 9))\n"
        "    Path('inside-write').write_text('allowed')\n"
        "    code = 'from pathlib import Path; Path(' + repr(str(secret)) + ').read_text()'\n"
        "    child = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True)\n"
        "    assert child.returncode != 0 and b'PermissionError' in child.stderr\n"
    )
    result = run(job, tmp_path)
    assert result["outcome"] == "pass", result
    assert sentinel.read_text() == "do not expose"
    assert not outside.exists()


def test_tooling_closure_uses_metadata_and_excludes_extras(tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    verifier = importlib.import_module("sew.gap.verify")
    source = tmp_path / "installed"
    source.mkdir()
    (source / "py.py").write_text("# pytest-owned compatibility module")
    (source / "support.py").write_text("# required dependency")
    packages = {
        "pytest": SimpleNamespace(
            metadata={"Name": "pytest"},
            files=[Path("py.py"), Path("../bin/pytest")],
            requires=["support>=1", 'missing-extra; extra == "testing"'],
            locate_file=lambda path: source / path,
        ),
        "support": SimpleNamespace(
            metadata={"Name": "support"},
            files=[Path("support.py")],
            requires=["pytest"],
            locate_file=lambda path: source / path,
        ),
    }
    monkeypatch.setattr(verifier.importlib.metadata, "distribution", packages.__getitem__)
    site = tmp_path / "site"
    verifier._copy_verifier_tooling(site)
    assert sorted(p.name for p in site.iterdir()) == ["py.py", "support.py"]
    assert not (tmp_path / "bin").exists()


def test_tests_cannot_replace_verifier_tooling(job, tmp_path):
    (job[2] / "test_visible.py").write_text(
        "import sys\nfrom pathlib import Path\nimport pytest\n"
        "def test_tooling_is_read_only():\n"
        "    import _pytest\n"
        "    target = Path(_pytest.__file__)\n"
        "    link = Path('tooling-link'); link.symlink_to(target)\n"
        "    for operation in [lambda: target.write_text('hijacked'),\n"
        "                      lambda: link.write_text('hijacked'),\n"
        "                      lambda: Path(sys.prefix).rename(Path(sys.prefix).with_name('stolen'))]:\n"
        "        with pytest.raises(PermissionError): operation()\n"
    )
    result = run(job, tmp_path)
    assert result["visible"][0]["passed"], result
    # A read-only runner must still detect the unfixed migration in the hidden lane.
    assert result["outcome"] == "fail"
    assert result["escaped_defects"] == 1


@pytest.mark.parametrize("attempt", range(10))
@pytest.mark.parametrize("mode", ["clean", "failure", "timeout"])
@pytest.mark.parametrize("backend", ["waitid", "kqueue"])
def test_execute_signals_before_reaping(tmp_path, monkeypatch, mode, attempt, backend):
    import importlib

    verifier = importlib.import_module("sew.gap.verify")
    if backend == "kqueue":
        if sys.platform != "darwin":
            pytest.skip("kqueue fallback is for macOS Python 3.11/3.12")
        monkeypatch.delattr(verifier.os, "waitid", raising=False)
    elif not hasattr(verifier.os, "waitid"):
        pytest.skip("waitid is unavailable on this Python/platform")
    spawn = verifier.subprocess.Popen
    killpg = verifier.os.killpg
    children = []

    def observe_spawn(*args, **kwargs):
        child = spawn(*args, **kwargs)
        children.append(child)
        return child

    def observe_signal(pgid, sig):
        assert pgid == children[0].pid
        assert children[0].returncode is None, "leader was reaped before killpg"
        killpg(pgid, sig)

    monkeypatch.setattr(verifier.subprocess, "Popen", observe_spawn)
    monkeypatch.setattr(verifier.os, "killpg", observe_signal)
    script = {
        "clean": "pass",
        "failure": "raise SystemExit(1)",
        "timeout": "import time; time.sleep(60)",
    }[mode]
    try:
        result = verifier._execute([sys.executable, "-I", "-c", script], tmp_path, {}, 0.1)
        assert result["timed_out"] == (mode == "timeout")
        assert result["exit_code"] == {"clean": 0, "failure": 1, "timeout": -signal.SIGKILL}[mode]
    finally:
        for child in children:
            if child.returncode is None:
                killpg(child.pid, signal.SIGKILL)
                child.wait()


@pytest.mark.parametrize("plant", [False, True])
def test_visible_tests_cannot_overwrite_hidden_oracle(job, tmp_path, plant):
    (job[2] / "test_visible.py").write_text(
        "from pathlib import Path\n"
        "def test_attack():\n"
        "    hidden = Path('hidden')\n"
        "    assert not hidden.exists()\n"
        + (
            "    hidden.mkdir()\n    (hidden / 'test_hidden.py').write_text('def test_fake(): assert True\\n')\n"
            if plant
            else ""
        )
    )
    result = run(job, tmp_path)
    assert result["visible"][0]["passed"], result
    assert result["outcome"] == "fail"
    if plant:
        assert "collide with hidden assets" in result["errors"][0]["message"]
        assert not result["hidden"]
    else:
        assert result["escaped_defects"] == 1


def test_patch_failure_preserves_output(tmp_path, monkeypatch):
    import importlib

    verifier = importlib.import_module("sew.gap.verify")
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    patch = tmp_path / "patch"
    patch.write_text("invalid")
    monkeypatch.setattr(verifier, "prepare_workspace", lambda *args, **kwargs: (None, cwd, {}))
    monkeypatch.setattr(verifier.sys, "platform", "darwin")
    from sew.gap import sandbox

    monkeypatch.setattr(
        sandbox, "select_backend",
        lambda env=None: sandbox.SandboxBackend("seatbelt", "/fixture/sandbox-exec"),
    )
    monkeypatch.setattr(verifier.subprocess, "check_output", lambda *args, **kwargs: "/usr/bin/git")
    monkeypatch.setattr(
        verifier,
        "_execute",
        lambda *args, **kwargs: dict(
            exit_code=1, timed_out=False, output="specific git diagnostic"
        ),
    )
    result = verifier.verify({"verifier": {"timeout_seconds": 1}}, patch, root=tmp_path)
    assert result["errors"][0]["message"] == "GAP patch application failed: specific git diagnostic"
