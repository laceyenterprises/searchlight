from app import query


def test_repeated_values():
    assert query({"tag": ["a b", "c&d"]}) == "tag=a+b&tag=c%26d"
