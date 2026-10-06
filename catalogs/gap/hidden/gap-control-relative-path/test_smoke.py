import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tempfile; from pathlib import Path; from app import resolve; base=Path(tempfile.gettempdir()); assert resolve(base,'a')==base.resolve()/'a'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
