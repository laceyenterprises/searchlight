from app import rows


def test_edge_cases(tmp_path):
    assert rows('name,note\nA,"hello, world"') == [["name", "note"], ["A", "hello, world"]]
    assert rows('"a""b",') == [['a"b', ""]]
