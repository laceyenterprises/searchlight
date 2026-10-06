import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tempfile; from pathlib import Path; from app import read_member; p=Path(tempfile.gettempdir())/'smoke.pem'; p.write_bytes(b'smoke'); assert read_member(str(p))==b'smoke'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
