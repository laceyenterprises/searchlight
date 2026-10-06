import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [sys.executable, "-c", "from app import rows; assert rows('a,\"b,c\"')==[['a','b,c']]"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
