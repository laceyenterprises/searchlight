import pytest
from app import refund_record


def test_empty_payload():
    with pytest.raises(ValueError, match="empty refund"):
        refund_record({})
