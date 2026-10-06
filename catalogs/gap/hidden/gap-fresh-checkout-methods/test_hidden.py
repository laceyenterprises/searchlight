import stripe
from app import create


def test_recorded_request_contract(monkeypatch):
    client = stripe.StripeClient("offline-test-key")
    calls = []
    service = client.v1.checkout.sessions

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        # Synthetic response conforming to the published object/list contract.
        return stripe.checkout.Session.construct_from(
            {
                "id": "cs_test",
                "object": "checkout.session",
                "mode": "payment",
                "allowed_payment_method_types": ["card"],
            },
            "offline-test-key",
        )

    monkeypatch.setattr(service, "_request", request)
    assert create(client, ["card"]) is not None
    assert calls[0][:2] == ("post", "/v1/checkout/sessions")
    assert calls[0][2]["params"] == {"mode": "payment", "allowed_payment_method_types": ["card"]}
