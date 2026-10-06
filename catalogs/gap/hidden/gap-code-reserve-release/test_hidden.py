import json
from pathlib import Path
import stripe
from app import value


class RecordedAPI:
    def get(self):
        return json.loads(Path(__file__).with_name("response.json").read_text())


def test_recorded_schema():
    # In-process stub: Seatbelt denies sockets. This replay follows the published schema.
    record = stripe.reserve.Release.construct_from(RecordedAPI().get(), "offline-test-key")
    assert value(record) == "expired"


def test_original_contract():
    record = stripe.reserve.Release.construct_from(
        {"reason": "hold_released_early"}, "offline-test-key"
    )
    assert value(record) == "requested"
