import stripe
from app import customer_id
from types import SimpleNamespace


def test_sdk_customer_read(monkeypatch):
    client = stripe.StripeClient("offline-test-key")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return SimpleNamespace(id=path.rsplit("/", 1)[-1])

    monkeypatch.setattr(client.v1.customers, "_request", request)
    assert customer_id(client, "cus_b", {"idempotency_key": "read-b"}) == "cus_b"
    assert calls[0][0:2] == ("get", "/v1/customers/cus_b")
    assert calls[0][2]["options"] == {"idempotency_key": "read-b"}
