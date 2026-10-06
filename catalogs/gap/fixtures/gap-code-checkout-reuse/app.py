def value(record):
    raw = record["payment_method_options"]["bancontact"]["setup_future_usage"]
    if raw != "none":
        raise ValueError("unrecognized reuse mode")
    return False
