from app import preview


def test_bad_color_is_plain_text():
    assert preview("ready", "operator-typo") == "ready"
    assert preview("ready", -1) == "ready"
