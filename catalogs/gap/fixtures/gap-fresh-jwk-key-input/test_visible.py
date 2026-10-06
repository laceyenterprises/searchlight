def test_unchanged_contract():
    import base64
    import jwt
    from app import sign

    k = b"synthetic-test-secret-at-least-32-bytes"
    d = {"kty": "oct", "k": base64.urlsafe_b64encode(k).decode().rstrip("=")}
    assert jwt.decode(sign("test", d), k, algorithms=["HS256"])["sub"] == "test"
