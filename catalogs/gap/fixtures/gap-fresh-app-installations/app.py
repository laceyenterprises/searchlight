def create(client, value):
    if not value:
        return None
    return client.v1.apps.secrets.list(params={"app": value})
