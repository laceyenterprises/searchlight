"""Exercise CI shell boundaries without downloads, sudo or firewall changes."""

import importlib.util
import json
import os
import runpy
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_scope", ROOT / "scripts/ci_scope.py")
ci_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scope)


def executable(path, body):
    path.write_text("#!/bin/bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("github_actions", ["true", "false", None])
@pytest.mark.parametrize("selected", [[], ["tests/test_example.py"]])
def test_clone_proof_reaches_macos_offline_suite_only_on_actions(
    tmp_path, github_actions, selected
):
    commands = tmp_path / "commands"
    commands.mkdir()
    # Exercise the real offline runner and its guard, stubbing privileged commands.
    executable(
        commands / "git",
        f"""if [[ "$*" == *clone* ]]; then
  mkdir -p "${{@: -1}}/scripts"
  cp {shlex.quote(str(ROOT / "scripts/test-offline.sh"))} "${{@: -1}}/scripts/"
fi
if [[ "$*" == *rev-parse* ]]; then echo deadbeef; fi
""",
    )
    tools = tmp_path / "venv-tools"
    tools.mkdir()
    executable(
        commands / "python3",
        f"""if [[ "$*" == "-m pip cache dir" ]]; then
  echo /tmp/synthetic-pip-cache
  exit
fi
mkdir -p "${{@: -1}}/bin"
cp {shlex.quote(str(tools))}/* "${{@: -1}}/bin/"
""",
    )
    executable(tools / "uname", "echo Darwin\n")
    executable(
        tools / "sudo", 'if [[ "$1" == --preserve-env=* ]]; then shift; fi\nexec "$@"\n'
    )
    executable(
        tools / "pfctl", 'if [[ "$1" == -s ]]; then echo "Status: Disabled"; fi\n'
    )
    executable(tools / "sew", "echo 'mode         standalone'\n")
    executable(
        tools / "python3",
        """[[ "${SYNTHETIC_PARENT_SECRET+x}" == "" ]]
[[ "$SEW_MODE" == standalone ]]
[[ "$SEW_TEST_INSTALLED" == 1 ]]
[[ "$PIP_CACHE_DIR" == /tmp/synthetic-pip-cache ]]
if [[ "$*" == *pytest* ]]; then
  [[ "$SEW_OFFLINE_TESTS" == 1 && "$SEW_REQUIRE_SEATBELT" == 1 ]]
  [[ "$PYTEST_DISABLE_PLUGIN_AUTOLOAD" == 1 ]]
  [[ "$*" == *"-p xdist.plugin -n auto --durations=30"* ]]
  if [[ "$*" == *tests/test_example.py* ]]; then echo 'selected file reached'; fi
  echo 'offline pytest reached'
fi
""",
    )
    # A minimal parent makes unset/false cases independent of the test host.
    environment = {
        "PATH": f"{commands}:/usr/bin:/bin",
        "SEW_PUBLICATION_BATTERY": "0",
        "SYNTHETIC_PARENT_SECRET": "synthetic-parent-value",
    }
    if github_actions is not None:
        environment["GITHUB_ACTIONS"] = github_actions
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/public-clone-proof.sh"), *selected],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if github_actions == "true":
        assert result.returncode == 0, result.stdout + result.stderr
        assert ("selected file reached" in result.stdout) == bool(selected)
        assert "offline pytest reached" in result.stdout
        assert "public-clone proof passed" in result.stdout
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "requires a disposable hosted runner" in result.stderr
        assert "offline pytest reached" not in result.stdout


@pytest.mark.parametrize(
    "output,exit_code,annotations",
    [
        ("suite passed\n", 0, []),
        (
            "FAILED tests/test_example.py::test_example - assertion\n",
            1,
            ["FAILED tests/test_example.py::test_example - assertion"],
        ),
        (
            "  \x1b[31mFAILED tests/test_example.py::test_example - 50%\x1b[0m\n",
            1,
            ["FAILED tests/test_example.py::test_example - 50%25"],
        ),
        (
            "E   AssertionError: example\nERROR tests/test_example.py\n",
            1,
            ["E   AssertionError: example", "ERROR tests/test_example.py"],
        ),
        (
            (
                "    E = example\n    E is not None\n    E   = example\n"
                "\x1b[31mE   AssertionError: example\x1b[0m\n"
            ),
            1,
            ["E   AssertionError: example"],
        ),
        (
            "".join(f"E   AssertionError: {index}\n" for index in range(20))
            + "FAILED tests/test_example.py::test_example\nERROR tests/test_setup.py\n",
            1,
            [f"E   AssertionError: {index}" for index in range(12, 20)]
            + [
                "FAILED tests/test_example.py::test_example",
                "ERROR tests/test_setup.py",
            ],
        ),
        (
            "".join(f"setup diagnostic {index}\n" for index in range(25)),
            1,
            [f"setup diagnostic {index}" for index in range(15, 25)],
        ),
        (
            "macOS offline runner requires a disposable hosted runner\n",
            1,
            ["macOS offline runner requires a disposable hosted runner"],
        ),
        ("", 1, ["Standalone clone proof failed without output"]),
    ],
)
def test_suite_step_reports_failures(tmp_path, output, exit_code, annotations):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    executable(
        scripts / "public-clone-proof.sh",
        f"printf '%s' {shlex.quote(output)}\nexit {exit_code}\n",
    )
    # Match the workflow's python3 to this test interpreter on either platform.
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "python3").symlink_to(sys.executable)
    workflow = yaml.safe_load((ROOT / ".github/workflows/standalone.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["standalone"]["steps"]
        if step.get("name") == "Fresh installed clone suite without external network"
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{commands}:/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path),
            "CI_EVENT": "pull_request",
            "TEST_FILES": "[]",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == exit_code, result.stderr
    assert output in result.stdout
    assert [
        line.removeprefix("::error::")
        for line in result.stdout.splitlines()
        if line.startswith("::error::")
    ] == annotations


def test_main_uses_existing_smoke_without_entering_clone_suite(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "smoke.py").write_text("print('existing synthetic smoke reached')\n")
    executable(scripts / "public-clone-proof.sh", "exit 99\n")
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "python3").symlink_to(sys.executable)
    workflow = yaml.safe_load((ROOT / ".github/workflows/standalone.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["standalone"]["steps"]
        if step.get("name") == "Fresh installed clone suite without external network"
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{commands}:/usr/bin:/bin", "CI_EVENT": "push"},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "existing synthetic smoke reached"


@pytest.mark.parametrize(
    "paths,expected",
    [
        ([], False),
        (["docs/guide.md", "reports/example/summary.json"], True),
        (["reports/example/REPORT.md", "reports/example/card.png"], True),
        (["reports/2026-09-26-search-api-head-to-head/code/sets/S1.json"], False),
        (
            ["reports/2026-09-26-search-api-head-to-head/code/stage_c/sets/SV.json"],
            False,
        ),
        (
            [
                "reports/2026-09-26-search-api-head-to-head/data/stage-b/runs/SM/runs_m01.json"
            ],
            False,
        ),
        (["reports/2026-10-03-search-gap-bench/reproduce.md"], False),
        (["reports/2026-09-29-web-search-bakeoff/suite.yaml"], False),
        (["docs/architecture/STATE-MACHINES.md"], False),
        (["docs/guide.md", "reports/2026-09-29-web-search-bakeoff/suite.yaml"], False),
        (["docs/changes/SLCI-02.md", "lib/python/sew/cli.py"], False),
        (["docs/example.py"], True),
        (["reports/example/test.py"], False),
        (["reports/example/script.sh"], False),
        (["reports/example/index.html"], True),
        (["docs/fixture.json"], True),
        ([".github/workflows/standalone.yml"], False),
        (["pyproject.toml"], False),
        (["tests/test_ci_runners.py"], False),
        (["scripts/ci_scope.py"], False),
        (["catalogs/gap/tasks.json"], False),
        (["config/provider-arm-tools.yaml"], False),
        (["site/index.html"], True),
    ],
)
def test_documentation_scope_is_an_allowlist(paths, expected):
    assert ci_scope.documentation_only(paths) is expected


def test_scope_diff_keeps_deletions_and_renames(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.org")):
        subprocess.run(["git", "config", key, value], check=True)
    (tmp_path / "code.py").write_text("pass\n")
    subprocess.run(["git", "add", "code.py"], check=True)
    subprocess.run(["git", "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "docs").mkdir()
    subprocess.run(["git", "mv", "code.py", "docs/guide.md"], check=True)
    subprocess.run(["git", "commit", "-qm", "rename"], check=True)
    assert ci_scope.scope("pull_request", base, "HEAD", tmp_path)["mode"] == "full"
    assert (
        ci_scope.scope("pull_request", "missing-ref", "HEAD", tmp_path)["mode"]
        == "full"
    )
    assert ci_scope.full_suite("pull_request", "HEAD", "HEAD")
    assert ci_scope.full_suite("schedule", "", "")
    assert ci_scope.full_suite("workflow_dispatch", "", "")
    assert not ci_scope.full_suite("push", "", "")


@pytest.mark.parametrize(
    "event,oses,versions",
    [
        ("pull_request", ["ubuntu-latest"], ["3.12"]),
        ("push", ["ubuntu-latest"], ["3.12"]),
        ("schedule", ["ubuntu-latest", "macos-latest"], ["3.11", "3.12", "3.13"]),
        (
            "workflow_dispatch",
            ["ubuntu-latest", "macos-latest"],
            ["3.11", "3.12", "3.13"],
        ),
    ],
)
def test_workflow_event_matrices(event, oses, versions):
    from types import SimpleNamespace

    workflow = yaml.safe_load((ROOT / ".github/workflows/standalone.yml").read_text())
    matrix = workflow["jobs"]["standalone"]["strategy"]["matrix"]
    for axis, expected in (("os", oses), ("python", versions)):
        expression = matrix[axis].removeprefix("${{").removesuffix("}}").strip()
        expression = expression.replace("&&", "and").replace("||", "or")
        assert (
            eval(
                expression,
                {"__builtins__": {}, "fromJSON": json.loads},
                {"github": SimpleNamespace(event_name=event)},
            )
            == expected
        )


@pytest.mark.parametrize(
    "path, included, excluded",
    [
        (
            "scripts/searchlight_site/pages.py",
            "tests/test_build_site.py",
            "tests/test_gap_code_corpus.py",
        ),
        (
            "lib/python/sew/insight/dashboard.py",
            "tests/test_dashboard_leaderboard.py",
            "tests/test_gap_code_corpus.py",
        ),
        (
            "tests/test_build_site.py",
            "tests/test_build_site.py",
            "tests/test_gap_code_corpus.py",
        ),
    ],
)
def test_affected_test_selection(path, included, excluded):
    result = ci_scope.select([path])
    assert result["mode"] == "selected", result
    assert included in result["tests"]
    assert excluded not in result["tests"]


def test_pr_78_wording_change_skips_clone_proof():
    paths = [
        "reports/2026-11-11-software-factory-insight-bakeoff/summary.json",
        "reports/2026-11-11-software-factory-insight-bakeoff/REPORT.md",
        "reports/2026-11-11-software-factory-insight-bakeoff/dashboard.html",
        "site/study-2026-11-11-software-factory-insight-bakeoff.html",
    ]
    result = ci_scope.select(paths)
    assert result["mode"] == "content-only"
    assert not result["tests"] and not result["battery"]


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "tests/conftest.py",
        "tests/containment.py",
        ".github/workflows/standalone.yml",
        "unknown-input",
    ],
)
def test_unmapped_and_shared_inputs_run_full(path):
    assert ci_scope.select([path])["mode"] == "full"


def test_parse_error_runs_full(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/bad.py").write_text("invalid python!\n")
    assert ci_scope.select(["scripts/bad.py"], tmp_path)["mode"] == "full"


def test_path_loads_transitive_imports_and_data(tmp_path):
    for directory in ("scripts", "tests", "lib/python/sew"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "scripts/load.py").write_text("import sew.child\n")
    (tmp_path / "lib/python/sew/child.py").write_text(
        "INPUT = ROOT / 'tasks' / 'example.json'\n"
    )
    (tmp_path / "tests/test_load.py").write_text(
        "spec_from_file_location('load', ROOT / 'scripts' / 'load.py')\n"
    )
    (tmp_path / "tests/test_other.py").write_text("pass\n")
    for path in ("scripts/load.py", "lib/python/sew/child.py", "tasks/example.json"):
        result = ci_scope.select([path], tmp_path)
        assert result["mode"] == "selected", result
        assert list(result["tests"]) == ["tests/test_load.py"]
    assert ci_scope.select(["fixtures/unknown.json"], tmp_path)["mode"] == "full"


@pytest.mark.parametrize("directory", ["tests", "scripts"])
@pytest.mark.parametrize("qualified", [False, True])
def test_helper_import_names_select_the_importer(tmp_path, directory, qualified):
    (tmp_path / directory).mkdir()
    (tmp_path / "tests").mkdir(exist_ok=True)
    helper = f"{directory}/probe_helper.py"
    (tmp_path / helper).write_text("X = 1\n")
    module = f"{directory}.probe_helper" if qualified else "probe_helper"
    (tmp_path / "tests/test_probe_q.py").write_text(f"from {module} import X\n")
    result = ci_scope.select([helper], tmp_path)
    assert result["mode"] == "selected", result
    assert list(result["tests"]) == ["tests/test_probe_q.py"]


@pytest.mark.parametrize("suffix", [".py", ".sh", ".js", ".ts"])
def test_report_executable_runs_full(tmp_path, suffix):
    path = f"reports/probe/build{suffix}"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text("# synthetic build script\n")
    result = ci_scope.select([path], tmp_path)
    assert result["mode"] == "full", result
    assert result["reason"] == f"unmapped path: {path}"


def test_colliding_module_names_run_full_only_for_colliding_changes(tmp_path):
    for directory in ("scripts", "tests", "lib/python/sew"):
        (tmp_path / directory).mkdir(parents=True)
    for directory in ("scripts", "tests"):
        (tmp_path / f"{directory}/probe_dup.py").write_text("X = 1\n")
        (tmp_path / f"tests/test_{directory}.py").write_text(
            f"from {directory}.probe_dup import X\n"
        )
    (tmp_path / "tests/test_bare.py").write_text("from probe_dup import X\n")
    (tmp_path / "lib/python/sew/probe.py").write_text("pass\n")
    # An unrelated change must build the map without the former StopIteration.
    result = ci_scope.select(["lib/python/sew/probe.py"], tmp_path)
    assert result["mode"] == "selected", result
    closures, _ = ci_scope.dependency_map(tmp_path)
    for directory in ("scripts", "tests"):
        path = f"{directory}/probe_dup.py"
        assert path in closures[f"tests/test_{directory}.py"]
        assert path not in closures["tests/test_bare.py"]
        result = ci_scope.select([path], tmp_path)
        assert result["mode"] == "full", result
        assert result["reason"] == "ambiguous module name: probe_dup"


def test_pr_74_selects_build_site_without_gap_corpus():
    paths = [
        "design/glyphs/glyphs.html",
        "design/glyphs/index.json",
        "design/glyphs/searchlight_glyphs.py",
        "design/vocabulary.md",
        "scripts/build_site.py",
        "scripts/searchlight_site/components.py",
        "scripts/searchlight_site/data.py",
        "scripts/searchlight_site/pages.py",
        "site/index.html",
        "site/infographic.svg",
        "site/learn.html",
        "site/report-2026-09-26-search-api-head-to-head.html",
        "site/report-2026-10-05-agent-search-behavior.html",
        "site/results.html",
        "tests/test_build_site.py",
    ]
    result = ci_scope.select(paths)
    assert result["mode"] == "selected", result
    assert "tests/test_build_site.py" in result["tests"]
    assert "tests/test_gap_code_corpus.py" not in result["tests"]
    assert (
        "design/glyphs/searchlight_glyphs.py"
        in result["tests"]["tests/test_build_site.py"]
    )


def test_unreached_mapped_python_selects_nothing(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/orphan.py").write_text("pass\n")
    result = ci_scope.select(["scripts/orphan.py"], tmp_path)
    assert result["mode"] == "selected", result
    assert not result["tests"]
    assert "no test reaches scripts/orphan.py" in result["reason"]
    assert ci_scope.select(["scripts/deleted.py"], tmp_path)["mode"] == "full"


def test_empty_selection_skips_suite_and_logs_to_summary(tmp_path, monkeypatch, capsys):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/orphan.py").write_text("pass\n")
    selector = tmp_path / "scripts/ci_scope.py"
    selector.write_bytes((ROOT / "scripts/ci_scope.py").read_bytes())
    summary, output = tmp_path / "summary", tmp_path / "output"
    for key, value in {
        "CI_EVENT": "pull_request",
        "CI_BASE": "base",
        "CI_HEAD": "head",
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        subprocess, "check_output", lambda *args, **kwargs: b"scripts/orphan.py\0"
    )
    runpy.run_path(str(selector), run_name="__main__")
    assert "run_suite=false\n" in output.read_text()
    assert "no test reaches scripts/orphan.py" in summary.read_text()
    assert "no test reaches scripts/orphan.py" in capsys.readouterr().out


def test_broad_selection_stays_selected(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts/shared.py").write_text("pass\n")
    for number in range(5):
        (tmp_path / f"tests/test_{number}.py").write_text(
            "import shared\n" if number < 4 else "pass\n"
        )
    result = ci_scope.select(["scripts/shared.py"], tmp_path)
    assert result["mode"] == "selected", result
    assert len(result["tests"]) == 4


def test_gap_verify_selects_its_broad_dependency_closure():
    closures, _ = ci_scope.dependency_map(ROOT)
    assert "lib/python/sew/gap/verify.py" in closures["tests/test_gap_verify.py"]
    assert "lib/python/sew/gap/verify.py" in closures["tests/test_gap_code_corpus.py"]
    result = ci_scope.select(["lib/python/sew/gap/verify.py"])
    assert result["mode"] == "selected"
    assert (
        len(result["tests"])
        > len([p for p in closures if p.startswith("tests/test_")]) * 0.6
    )
    assert "tests/test_gap_code_corpus.py" in result["tests"]


def test_fixture_battery_tracks_inputs():
    assert not ci_scope.select(["scripts/searchlight_site/pages.py"])["battery"]
    assert ci_scope.select(["fixtures/example.json"])["battery"]
    assert ci_scope.select(["scripts/offline-fixture-battery.py"])["battery"]


def test_site_assets_select_their_readers():
    result = ci_scope.select(["scripts/site_assets/site.css"])
    assert result["mode"] == "selected"
    assert "tests/test_build_site.py" in result["tests"]
    assert "tests/test_gap_code_corpus.py" not in result["tests"]


def test_full_scope_does_not_run_unaffected_fixture_battery():
    assert not ci_scope.select([".github/workflows/standalone.yml"])["battery"]
