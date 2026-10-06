from app import customer_id
from types import SimpleNamespace


def test_adapter_contract():
    class Service:
        def retrieve(self, customer, options=None):
            assert options == {"idempotency_key": "job-1"}
            return SimpleNamespace(id=customer)

    client = SimpleNamespace(v1=SimpleNamespace(customers=Service()))
    assert customer_id(client, "cus_a", {"idempotency_key": "job-1"}) == "cus_a"
