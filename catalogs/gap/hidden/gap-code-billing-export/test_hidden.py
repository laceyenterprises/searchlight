import stripe
from app import export


def test_sdk_record():
    r = stripe.StripeObject.construct_from({"id": "a", "description": "manual charge"}, None)
    assert export(r) == {"id": "a", "description": "manual charge"}
    assert export(r)["id"] == "a"
