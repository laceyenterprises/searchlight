from importlib.metadata import version
from pathlib import Path
import json
from app import sign


def test_empty_symmetric_jwk():
    assert sign("test", {"kty": "oct", "k": ""}) is None


def test_offline_advisory():
    advisory = json.loads(Path(__file__).with_name("advisory.json").read_text())
    assert version("PyJWT") == advisory["fixed_version"]


def test_delivered_security_pin():
    assert "pyjwt==2.14.0" in Path("requirements.txt").read_text().splitlines()
