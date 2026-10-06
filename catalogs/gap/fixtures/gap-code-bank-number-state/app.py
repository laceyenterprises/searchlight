def value(record):
    raw = record["account_numbers"][0]["status"]
    return raw != "deactivated"
