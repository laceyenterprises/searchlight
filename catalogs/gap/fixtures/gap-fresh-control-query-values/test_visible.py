def test_unchanged_contract():
    from app import query

    assert query({"name": "a b"}) == "name=a+b"
