def test_unchanged_contract():
    import io
    from unittest.mock import patch
    from app import emit

    s = io.StringIO()
    with patch("sys.stdout", s):
        emit("ready")
    assert s.getvalue() == "ready\n"
