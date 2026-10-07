"""Capability decisions use stubbed tools, never a real sandbox or daemon."""

import subprocess

import pytest

from containment import probe_containment, require_capability


@pytest.mark.parametrize(
    "name,platform",
    [
        ("seatbelt", "darwin"),
        ("bubblewrap", "linux"),
        ("ps", "linux"),
        ("docker", "linux"),
    ],
)
@pytest.mark.parametrize("outcome", ["ok", "refused", "missing", "timeout", "denied"])
@pytest.mark.parametrize("mandatory", [False, True])
def test_capability_decision(monkeypatch, capsys, name, platform, outcome, mandatory):
    monkeypatch.setenv("SEW_REQUIRE_CONTAINMENT_TESTS", "1" if mandatory else "0")
    monkeypatch.delenv("SEW_REQUIRE_" + name.upper(), raising=False)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 10,
            "check": False,
        }
        if outcome == "missing":
            raise FileNotFoundError()
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 10)
        if outcome == "denied":
            raise PermissionError()
        return subprocess.CompletedProcess(
            command,
            int(outcome == "refused"),
            "",
            "sandbox-exec: sandbox_apply: Operation not permitted",
        )

    monkeypatch.setattr(subprocess, "run", run)
    capabilities = probe_containment(platform)
    capability = capabilities[name]
    assert len(calls) == 3
    if outcome == "ok":
        require_capability(capabilities, name)
        assert capability.available
    else:
        exception = pytest.fail.Exception if mandatory else pytest.skip.Exception
        with pytest.raises(
            exception,
            match="containment unavailable: "
            + capability.reason.replace("(", r"\(").replace(")", r"\)"),
        ):
            require_capability(capabilities, name)
        assert not capability.available
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "platform,name", [("linux", "seatbelt"), ("darwin", "bubblewrap")]
)
def test_other_platform_remains_skipped(monkeypatch, platform, name):
    monkeypatch.setenv("SEW_REQUIRE_CONTAINMENT_TESTS", "1")
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0)
    )
    with pytest.raises(pytest.skip.Exception, match="requires another platform"):
        require_capability(probe_containment(platform), name)


def test_probe_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kw: (
            calls.append(command) or subprocess.CompletedProcess(command, 0)
        ),
    )
    probe_containment("darwin")
    probe_containment("linux")
    assert calls == [
        ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
        ["ps", "-A", "-o", "pid="],
        ["docker", "info"],
        ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "/usr/bin/true"],
        ["ps", "-A", "-o", "pid="],
        ["docker", "info"],
    ]


pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    "available,mandatory", [(True, False), (False, False), (False, True)]
)
def test_session_probe_and_pytest_outcomes(pytester, monkeypatch, available, mandatory):
    from pathlib import Path

    monkeypatch.setenv("SEW_REQUIRE_CONTAINMENT_TESTS", "1" if mandatory else "0")
    monkeypatch.delenv("SEW_REQUIRE_SEATBELT", raising=False)
    monkeypatch.delenv("SEW_REQUIRE_BUBBLEWRAP", raising=False)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0 if available else 1, "", "sandbox_apply: Operation not permitted"
        )

    monkeypatch.setattr(subprocess, "run", run)
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text())
    pytester.makepyfile("""
        import pytest

        @pytest.mark.parametrize("case", [1, 2])
        def test_containment(require_containment, case):
            require_containment()
            assert case in (1, 2)
    """)
    result = pytester.runpytest("-q", "-rs")
    assert len(calls) == 3  # Both tests share one session probe.
    if available:
        result.assert_outcomes(passed=2)
    elif mandatory:
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(["*containment unavailable:*"])
    else:
        result.assert_outcomes(skipped=2)
        result.stdout.fnmatch_lines(["*containment unavailable:*"])
