from __future__ import annotations

import os
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
