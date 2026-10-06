"""`hq sew` must not run under whatever bare `python3` is first on PATH.

2026-09-26: on the reference host `python3` resolved to the Command Line Tools
3.9, which rejects `-P` ("Unknown option: -P"), so every `hq sew` command
failed. The wrapper now resolves its interpreter through the fleet resolver.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = MODULE_ROOT / "bin" / "hq-sew"


def test_wrapper_skips_a_python_too_old_for_dash_p(tmp_path: Path) -> None:
    stub = tmp_path / "python3"
    stub.write_text('#!/bin/sh\necho "Unknown option: -P" >&2\nexit 2\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HQ_PYTHON3_CANDIDATES_FOR_TEST": f"{stub} {sys.executable}",
    }

    completed = subprocess.run(
        ["bash", str(WRAPPER), "--help"], env=env, text=True, capture_output=True, timeout=120
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage: sew" in completed.stdout
    assert "Unknown option: -P" not in completed.stderr


def test_explicit_hq_python3_is_honored(tmp_path: Path) -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HQ_PYTHON3": sys.executable,
    }

    completed = subprocess.run(
        ["bash", str(WRAPPER), "--help"], env=env, text=True, capture_output=True, timeout=120
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage: sew" in completed.stdout
    assert os.path.exists(sys.executable)


@pytest.mark.parametrize("installed", ["absent", "broken-load", "broken-detect", "unavailable"])
def test_launcher_discovers_in_tree_sdk_without_usable_installed_metadata(
    tmp_path: Path, installed: str
) -> None:
    interpreter = tmp_path / "python-without-sdk-metadata"
    discovery = {
        "absent": "lambda **kw: []",
        "broken-load": "lambda **kw: [SimpleNamespace(load=broken)]",
        "broken-detect": "lambda **kw: [SimpleNamespace(load=lambda: lambda: SimpleNamespace(detect=broken))]",
        "unavailable": "lambda **kw: [SimpleNamespace(load=lambda: lambda: SimpleNamespace(detect=lambda: SimpleNamespace(available=False, reason='missing services')))]",
    }[installed]
    bootstrap = (
        "import importlib.metadata, runpy, sys\n"
        "from types import SimpleNamespace\n"
        "def broken():\n"
        "    raise ModuleNotFoundError('stale installed SDK')\n"
        f"importlib.metadata.entry_points = {discovery}\n"
        "sys.argv = ['hq-sew', *sys.argv[1:]]; "
        "runpy.run_module('sew.cli', run_name='__main__')"
    )
    interpreter.write_text(
        "#!/bin/bash\n"
        'if [[ "$1 $2 $3" == "-P -m sew.cli" ]]; then\n'
        f'  exec {shlex.quote(sys.executable)} -P -c {shlex.quote(bootstrap)} "${{@:4}}"\n'
        "fi\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    interpreter.chmod(0o755)
    sdk = tmp_path / "sdk/agent_os_app_sdk"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").write_text("")
    (sdk / "host.py").write_text(
        "import os\nfrom pathlib import Path\nfrom types import SimpleNamespace\n"
        "def sew_host():\n"
        "    return SimpleNamespace(mode='agent-os', detect=lambda: SimpleNamespace(available=True, reason='fixture'), "
        "state_root=lambda app_id='sew': Path(os.environ['HQ_ROOT']) / 'var' / app_id)\n"
    )
    deploy = tmp_path / "deploy"
    service = deploy / "modules/worker-pool/bin/hq-app-host"
    service.parent.mkdir(parents=True)
    service.touch(mode=0o755)
    hq = tmp_path / "hq"
    hq.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HQ_PYTHON3": str(interpreter),
        "AGENT_OS_ROOT": str(deploy),
        "SEW_AGENT_OS_SDK_PATH": str(sdk.parent),
        "HQ_ROOT": str(hq),
        "SEW_CONFIG": str(tmp_path / "missing.yaml"),
    }
    completed = subprocess.run(
        ["bash", str(WRAPPER), "doctor"],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "mode         agent-os" in completed.stdout
    assert f"state root   {hq / 'var/sew'}" in completed.stdout
