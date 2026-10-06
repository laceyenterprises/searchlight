import json
from pathlib import Path
import stripe
from app import value


class RecordedAPI:
    def get(self):
        return json.loads(Path(__file__).with_name("response.json").read_text())


def test_recorded_schema():
    # In-process stub: Seatbelt denies sockets. This replay follows the published schema.
    record = stripe.financial_connections.Account.construct_from(
        RecordedAPI().get(), "offline-test-key"
    )
    assert value(record) is False


def test_original_contract():
    record = stripe.financial_connections.Account.construct_from(
        {"account_numbers": [{"status": "deactivated"}]}, "offline-test-key"
    )
    assert value(record) is False


def test_waiting_and_ready():
    for status, expected in [("pending", False), ("transactable", True)]:
        record = stripe.financial_connections.Account.construct_from(
            {"account_numbers": [{"status": status}]}, "offline-test-key"
        )
        assert value(record) is expected
