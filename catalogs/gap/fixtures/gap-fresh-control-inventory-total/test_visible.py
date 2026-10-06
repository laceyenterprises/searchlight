def test_unchanged_contract():
    from app import total

    assert total([]) == {}
    assert total([("a", 2)]) == {"a": 2}
