from app import lookup


def test_native_record():
    assert lookup({"description": "manual"}) == "manual"
    assert lookup({}) == "unlabelled"
