import stripe
from app import lookup


def test_sdk_record():
    r = stripe.StripeObject.construct_from({"id": "a", "description": "manual charge"}, None)
    assert lookup(r) == "manual charge"
    assert lookup(stripe.StripeObject.construct_from({"id": "b"}, None)) == "unlabelled"
