import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import stripe; from app import value; assert value(stripe.checkout.Session.construct_from({"payment_method_options": {"bancontact": {"setup_future_usage": "off_session"}}},None))==True',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
