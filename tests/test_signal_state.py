"""Exercise the real test-session signal fixtures in disposable interpreters."""

from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from conftest import MODULE_ROOT


def _run_pytest(script: str) -> subprocess.CompletedProcess[str]:
    # A fresh interpreter prevents the deliberately ignored/leaked handler from
    # changing this pytest process. No host credentials or provider configuration.
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=MODULE_ROOT,
        env={"PATH": os.defpath, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        # Terminate the entire nested run, including sleeping fake harnesses.
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


# These node lists must remain ledger-free: confcutdir intentionally excludes
# the repo-root ledger isolation and worker-cap plugins.
def test_interrupt_tests_work_with_inherited_ignored_sigint() -> None:
    result = _run_pytest("""
import signal
import pytest
signal.signal(signal.SIGINT, signal.SIG_IGN)
code = pytest.main(['-q', '-p', 'no:cacheprovider', '--confcutdir=tests',
    'tests/test_live_runner.py::test_interrupt_and_resume_produce_no_duplicate_run_ids',
    'tests/test_live_runner.py::test_interrupted_live_cell_spend_is_charged_on_resume'])
assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
raise SystemExit(code)
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


@pytest.mark.parametrize("leak", ["signal.SIG_IGN", "lambda signum, frame: None"])
def test_sigint_guard_reports_leak_and_restores_handler(leak: str) -> None:
    result = _run_pytest(f"""
import signal
import pytest
signal.signal(signal.SIGINT, signal.SIG_IGN)
class Leak:
    def pytest_runtest_setup(self, item):
        if item.name.endswith('[True]'):
            assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    def pytest_runtest_call(self, item):
        if item.name.endswith('[-1]'):
            signal.signal(signal.SIGINT, {leak})
code = pytest.main(['-q', '-p', 'no:cacheprovider', '--confcutdir=tests',
    'tests/test_live_runner.py::test_max_provider_calls_must_be_a_non_negative_integer[-1]',
    'tests/test_live_runner.py::test_max_provider_calls_must_be_a_non_negative_integer[True]'],
    plugins=[Leak()])
assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
raise SystemExit(code)
""")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "test leaked a SIGINT handler" in result.stdout
    assert "2 passed, 1 error" in result.stdout
