def report(client, record, path, account):
    if not path:
        return None
    return record.request("get", path, stripe_account=account).to_dict()
