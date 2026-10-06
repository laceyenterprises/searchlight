import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import stripe; from app import export; assert export(stripe.StripeObject.construct_from({'id':'smoke'},None))=={'id':'smoke'}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
