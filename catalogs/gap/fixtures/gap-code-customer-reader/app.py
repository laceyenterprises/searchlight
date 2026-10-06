def customer_id(client, identifier, options=None):
    return client.v1.customers.retrieve(customer=identifier, options=options).id
