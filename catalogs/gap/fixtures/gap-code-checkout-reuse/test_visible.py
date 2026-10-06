from app import value


def test_original_contract():
    assert (
        value({"payment_method_options": {"bancontact": {"setup_future_usage": "none"}}}) is False
    )
