from app import value


def test_original_contract():
    assert value({"payment_method_details": {"card": {"mandate": "mandate_a"}}}) == "mandate_a"
