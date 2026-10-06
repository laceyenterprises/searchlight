import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import refund_record; assert refund_record({'id':'trr_smoke','amount':3}).amount == 3",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
