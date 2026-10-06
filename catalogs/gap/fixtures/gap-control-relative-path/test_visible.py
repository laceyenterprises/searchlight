from app import resolve


def test_visible(tmp_path):
    assert resolve(tmp_path, "report.txt") == tmp_path / "report.txt"
