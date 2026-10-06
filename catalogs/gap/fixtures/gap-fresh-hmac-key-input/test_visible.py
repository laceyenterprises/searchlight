def test_unchanged_contract():
    import jwt
    from app import sign

    k = "synthetic-test-secret-at-least-32-bytes"
    assert jwt.decode(sign("test", k), k, algorithms=["HS256"])["sub"] == "test"
