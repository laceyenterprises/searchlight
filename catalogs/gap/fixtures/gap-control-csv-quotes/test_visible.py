from app import rows


def test_visible(tmp_path):
    assert rows("a,b\n1,2") == [["a", "b"], ["1", "2"]]
