"""Exercise CI shell boundaries without downloads, sudo or firewall changes."""

import importlib.util
import json
import os
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
def test_clone_proof_reaches_macos_offline_suite_only_on_actions(
    tmp_path, github_actions
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
        ["bash", str(ROOT / "scripts/public-clone-proof.sh")],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if github_actions == "true":
        assert result.returncode == 0, result.stdout + result.stderr
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
        (["docs/changes/SLCI-02.md", "lib/python/sew/cli.py"], False),
        (["docs/example.py"], False),
        (["reports/example/test.py"], False),
        (["reports/example/script.sh"], False),
        (["reports/example/index.html"], False),
        (["docs/fixture.json"], False),
        ([".github/workflows/standalone.yml"], False),
        (["pyproject.toml"], False),
        (["tests/test_ci_runners.py"], False),
        (["scripts/ci_scope.py"], False),
        (["catalogs/gap/tasks.json"], False),
        (["config/provider-arm-tools.yaml"], False),
        (["site/index.html"], False),
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
    assert ci_scope.full_suite("pull_request", base, "HEAD")
    assert ci_scope.full_suite("pull_request", "missing-ref", "HEAD")
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
