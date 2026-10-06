from app import resolve


def test_edge_cases(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        resolve(tmp_path, "../outside.txt")
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve(tmp_path, "link/outside.txt")
