def test_unchanged_contract():
    from app import create

    assert create(None, "") is None
