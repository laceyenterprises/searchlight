def create(client, value):
    if not value:
        return None
    return client.v1.financial_connections.sessions.create(
        params={
            "account_holder": {"type": "customer", "customer": "cus_test"},
            "permissions": ["payment_method"],
            "filters": {"countries": [value]},
        }
    )
