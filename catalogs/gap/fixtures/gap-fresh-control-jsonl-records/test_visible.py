def test_unchanged_contract():
    from app import encode

    assert encode([]) == ""
