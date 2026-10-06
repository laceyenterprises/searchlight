import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import stripe; from app import value; assert value(stripe.Charge.construct_from({"payment_method_details": {"card": {"mandate": {"object": "mandate", "id": "mandate_b"}}}},None))==\'mandate_b\'',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
