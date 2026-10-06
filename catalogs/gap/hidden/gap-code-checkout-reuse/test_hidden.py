import json
from pathlib import Path
import stripe
from app import value


class RecordedAPI:
    def get(self):
        return json.loads(Path(__file__).with_name("response.json").read_text())


def test_recorded_schema():
    # In-process stub: Seatbelt denies sockets. This replay follows the published schema.
    record = stripe.checkout.Session.construct_from(RecordedAPI().get(), "offline-test-key")
    assert value(record) is True


def test_original_contract():
    record = stripe.checkout.Session.construct_from(
        {"payment_method_options": {"bancontact": {"setup_future_usage": "none"}}},
        "offline-test-key",
    )
    assert value(record) is False
