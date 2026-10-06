import stripe


def remove(identifier, account):
    if not identifier:
        return None
    return stripe.Coupon.delete(sid=identifier, stripe_account=account).deleted
