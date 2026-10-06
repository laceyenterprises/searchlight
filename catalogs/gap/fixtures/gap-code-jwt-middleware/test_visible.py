import jwt
from app import claims

KEY = "test-key-with-at-least-thirty-two-bytes"


def test_signed_claims():
    assert claims(jwt.encode({"sub": "a"}, KEY, algorithm="HS256"), KEY)["sub"] == "a"


def test_wrong_signature():
    assert claims(jwt.encode({"sub": "a"}, KEY, algorithm="HS256"), KEY + "other") is None
