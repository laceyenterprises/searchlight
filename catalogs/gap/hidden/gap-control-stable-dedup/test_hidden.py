from app import unique


def test_edge_cases(tmp_path):
    values = ["z", "a", "m", "z", "q"]
    assert unique(values) == ["z", "a", "m", "q"]
    assert unique([]) == []
