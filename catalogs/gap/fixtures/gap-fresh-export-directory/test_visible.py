def test_unchanged_contract():
    from app import export

    assert export("ready") == "ready"
