import stripe
from app import create


def test_recorded_request_contract(monkeypatch):
    client = stripe.StripeClient("offline-test-key")
    calls = []
    service = client.v1.apps.installs

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        # Synthetic response conforming to the published object/list contract.
        return stripe.ListObject.construct_from(
            {"object": "list", "data": [], "has_more": False, "url": "/v1/apps/installs"},
            "offline-test-key",
        )

    monkeypatch.setattr(service, "_request", request)
    assert create(client, "app_test") is not None
    assert calls[0][:2] == ("get", "/v1/apps/installs")
    assert calls[0][2]["params"] == {"app": "app_test"}
