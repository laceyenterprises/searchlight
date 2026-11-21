"""Report selection regressions, run with pytest scripts/test_ci_scope.py."""

import pytest

from ci_scope import select


@pytest.mark.parametrize("name", ["run", "run.bash", "run.rb", "run.PY"])
def test_report_programs_run_full_suite(tmp_path, name):
    path = f"reports/bench/{name}"
    executable = tmp_path / path
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    result = select([path], tmp_path)
    assert result["mode"] == "full"
    assert result["reason"] == f"unmapped path: {path}"


def test_report_publication_assets_still_skip_suite(tmp_path):
    result = select(
        [
            "reports/bench/REPORT.md",
            "reports/bench/summary.json",
            "reports/bench/dashboard.html",
            "reports/bench/chart.PNG",
        ],
        tmp_path,
    )
    assert result["mode"] == "content-only"
