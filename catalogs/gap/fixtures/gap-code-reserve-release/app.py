def value(record):
    raw = record["reason"]
    return {"bulk_hold_expiry": "expired", "hold_released_early": "requested"}.get(raw, "unknown")
