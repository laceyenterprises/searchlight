import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [sys.executable, "-c", "from app import unique; assert unique(['b','a','b'])==['b','a']"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
