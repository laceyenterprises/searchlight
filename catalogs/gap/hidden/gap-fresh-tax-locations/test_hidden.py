import stripe
from app import create


def test_recorded_request_contract(monkeypatch):
    client = stripe.StripeClient("offline-test-key")
    calls = []
    service = client.v1.tax.locations

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        # Synthetic response conforming to the published object/list contract.
        return stripe.tax.Location.construct_from(
            {"id": "taxloc_test", "object": "tax.location", "address": {"country": "US"}},
            "offline-test-key",
        )

    monkeypatch.setattr(service, "_request", request)
    assert create(client, "taxloc_test") is not None
    assert calls[0][:2] == ("get", "/v1/tax/locations/taxloc_test")
    assert calls[0][2]["params"] is None
