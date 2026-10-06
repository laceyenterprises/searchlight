def create(client, value):
    if not value:
        return None
    return client.v1.tax.registrations.retrieve(value)
