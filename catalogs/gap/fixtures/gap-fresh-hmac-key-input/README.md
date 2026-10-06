Harden the local token signer used by the test identity service. Return a signed token for a valid shared secret, or None for rejected key material, so malformed configuration cannot crash startup. Ship a secure dependency pin.

Run `python -m pytest test_visible.py`. Edit app.py and requirements.txt.
