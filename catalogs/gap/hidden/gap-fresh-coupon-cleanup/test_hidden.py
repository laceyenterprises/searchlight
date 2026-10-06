import stripe
from app import remove


def test_coupon_delete(monkeypatch):
    calls = []

    def request(method, path, params=None, **kwargs):
        calls.append((method, path, params))
        return stripe.Coupon.construct_from(
            {"id": "coupon_test", "deleted": True}, "offline-test-key"
        )

    monkeypatch.setattr(stripe.Coupon, "_static_request", request)
    assert remove("coupon_test", "acct_test") is True
    assert calls == [("delete", "/v1/coupons/coupon_test", {"stripe_account": "acct_test"})]
