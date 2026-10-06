from app import value


def test_original_contract():
    assert value({"reason": "hold_released_early"}) == "requested"
