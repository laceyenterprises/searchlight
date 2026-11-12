"""Run with python3 -m pytest scripts/test_ci_scope.py."""

import importlib.util
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    "ci_scope", Path(__file__).with_name("ci_scope.py")
)
ci_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scope)


@pytest.mark.parametrize(
    "path",
    [
        "reports/2026-09-26-search-api-head-to-head/code/sets/S1.json",
        "reports/2026-09-26-search-api-head-to-head/code/stage_c/sets/SV.json",
        "reports/2026-09-26-search-api-head-to-head/data/stage-b/runs/SM/runs_m01.json",
        "reports/2026-10-03-search-gap-bench/reproduce.md",
        "reports/2026-09-29-web-search-bakeoff/suite.yaml",
        "docs/architecture/STATE-MACHINES.md",
    ],
)
def test_suite_integrity_inputs_select_full_suite(path, tmp_path, monkeypatch):
    """A real PR diff must select the tests that validate these inputs."""
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    git("init", "-q")
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text("original\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.org",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    target.write_text("changed\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.org",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "change")

    assert not ci_scope.documentation_only([path])
    assert not ci_scope.documentation_only(["docs/guide.md", path])
    assert ci_scope.full_suite("pull_request", base, "HEAD")


@pytest.mark.parametrize(
    "path",
    [
        "docs/guide.md",
        "reports/example/REPORT.md",
        "reports/example/summary.json",
        "reports/example/card.png",
    ],
)
def test_publication_gated_paths_can_still_omit_suite(path):
    assert ci_scope.documentation_only([path])
