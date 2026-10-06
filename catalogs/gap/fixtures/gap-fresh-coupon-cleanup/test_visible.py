def test_unchanged_contract():
    from app import remove

    assert remove("", "acct_test") is None
