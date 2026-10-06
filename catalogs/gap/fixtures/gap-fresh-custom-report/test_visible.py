def test_unchanged_contract():
    from app import report

    assert report(None, None, "", "acct_test") is None
