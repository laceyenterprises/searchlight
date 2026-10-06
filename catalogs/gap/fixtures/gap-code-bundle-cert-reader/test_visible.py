from app import read_member


def test_ordinary_file(tmp_path):
    p = tmp_path / "ordinary.pem"
    p.write_bytes(b"ordinary certificate")
    assert read_member(str(p)) == b"ordinary certificate"
