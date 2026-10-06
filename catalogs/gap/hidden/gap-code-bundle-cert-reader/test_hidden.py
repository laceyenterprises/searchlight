import json
import zipfile
from pathlib import Path
from importlib.metadata import version
from app import read_member


def test_offline_advisory():
    advisory = json.loads(Path(__file__).with_name("advisory.json").read_text())
    assert tuple(map(int, version("requests").split(".")[:2])) >= tuple(advisory["fixed"])


def test_delivered_security_pin():
    requirements = Path("requirements.txt")
    assert requirements.is_file(), "security repair must deliver requirements"
    pins = [line.strip() for line in requirements.read_text().splitlines()]
    assert [pin for pin in pins if pin.startswith("requests==")] == ["requests==2.33.0"]


def test_zip_member(tmp_path):
    archive = tmp_path / "certs.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("certs/root.pem", b"synthetic-certificate")
    assert read_member(str(archive / "certs/root.pem")) == b"synthetic-certificate"


def test_existing_file(tmp_path):
    p = tmp_path / "root.pem"
    p.write_bytes(b"another-certificate")
    assert read_member(str(p)) == b"another-certificate"
