from app import refund_record


def test_refund_resource():
    r = refund_record({"id": "trr_17", "object": "transfer_reversal", "amount": 230})
    assert r.id == "trr_17"
    assert r.amount == 230


def test_distinct_refunds():
    assert refund_record({"id": "trr_18", "object": "transfer_reversal"}).id == "trr_18"
