from app import option


def test_option_name():
    assert option().name == "mode"
