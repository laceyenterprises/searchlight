def test_unchanged_contract():
    import io
    from app import prepare

    b = io.BytesIO(b"abc")
    b.read()
    assert prepare(b, 0) is True
    assert b.read() == b"abc"
