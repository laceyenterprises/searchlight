from app import total


def test_repeated_sku():
    assert list(total([("b", 2), ("a", 1), ("b", 3)]).items()) == [("b", 5), ("a", 1)]
