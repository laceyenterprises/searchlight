from app import value


def test_original_contract():
    assert value({"account_numbers": [{"status": "deactivated"}]}) is False
