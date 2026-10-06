from app import unique


def test_visible(tmp_path):
    assert sorted(unique(["a", "a", "b"])) == ["a", "b"]
