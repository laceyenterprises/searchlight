def value(record):
    raw = record["payment_method_details"]["card"]["mandate"]
    return raw
