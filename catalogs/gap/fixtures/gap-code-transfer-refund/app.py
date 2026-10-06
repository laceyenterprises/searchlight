import stripe


def refund_record(payload):
    if not payload:
        raise ValueError("empty refund")
    return stripe.Reversal.construct_from(payload, "offline-test-key")
