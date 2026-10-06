import stripe
from app import create


def test_recorded_request_contract(monkeypatch):
    client = stripe.StripeClient("offline-test-key")
    calls = []
    service = client.v1.financial_connections.sessions

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        # Synthetic response conforming to the published object/list contract.
        return stripe.financial_connections.Session.construct_from(
            {
                "id": "fcsess_test",
                "object": "financial_connections.session",
                "permissions": ["payment_method"],
            },
            "offline-test-key",
        )

    monkeypatch.setattr(service, "_request", request)
    assert create(client, "US") is not None
    assert calls[0][:2] == ("post", "/v1/financial_connections/sessions")
    assert calls[0][2]["params"] == {
        "account_holder": {"type": "customer", "customer": "cus_test"},
        "permissions": ["payment_method"],
        "filters": {"country": "US"},
    }
