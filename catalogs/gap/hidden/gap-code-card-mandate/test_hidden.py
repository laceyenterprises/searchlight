import json
from pathlib import Path
import stripe
import pytest
from app import value


class RecordedAPI:
    def get(self):
        return json.loads(Path(__file__).with_name("response.json").read_text())


def test_recorded_schema():
    # In-process stub: Seatbelt denies sockets. This replay follows the published schema.
    record = stripe.Charge.construct_from(RecordedAPI().get(), "offline-test-key")
    assert value(record) == "mandate_b"


def test_original_contract():
    record = stripe.Charge.construct_from(
        {"payment_method_details": {"card": {"mandate": "mandate_a"}}}, "offline-test-key"
    )
    assert value(record) == "mandate_a"


@pytest.mark.parametrize("card", [{"mandate": None}, {}], ids=["null", "absent"])
def test_no_mandate(card):
    response = {"payment_method_details": {"card": card}}
    record = stripe.Charge.construct_from(response, "offline-test-key")
    assert value(record) is None
    assert value(response) is None
