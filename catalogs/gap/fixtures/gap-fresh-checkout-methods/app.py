def create(client, value):
    if not value:
        return None
    return client.v1.checkout.sessions.create(
        params={"mode": "payment", "payment_method_types": value}
    )
