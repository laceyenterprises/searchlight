import json
import stripe
from app import report


def test_report_scope(monkeypatch):
    client = stripe.StripeClient("offline-test-key")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return json.dumps({"total": 17}), 200, {}

    monkeypatch.setattr(client._requestor, "request_raw", request)
    record = stripe.StripeObject.construct_from({}, "offline-test-key")
    assert report(client, record, "/v1/reports/example", "acct_test") == {"total": 17}
    assert calls[0][:2] == ("get", "/v1/reports/example")
    assert calls[0][2]["options"]["stripe_account"] == "acct_test"
