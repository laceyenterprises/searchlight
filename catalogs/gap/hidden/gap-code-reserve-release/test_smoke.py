import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import stripe; from app import value; assert value(stripe.reserve.Release.construct_from({"reason": "hold_expired"},None))==\'expired\'',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
