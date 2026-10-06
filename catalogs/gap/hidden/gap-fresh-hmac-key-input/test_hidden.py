from importlib.metadata import version
from pathlib import Path
import json
from app import sign


def test_public_key_document_is_not_a_secret():
    # Synthetic schema example, not a live identity-provider key.
    jwk = {"kty": "RSA", "n": "AQAB", "e": "AQAB"}
    assert sign("test", json.dumps([jwk])) is None
    assert sign("test", "\ufeff" + json.dumps(jwk)) is None


def test_offline_advisory():
    advisory = json.loads(Path(__file__).with_name("advisory.json").read_text())
    assert version("PyJWT") == advisory["fixed_version"]


def test_delivered_security_pin():
    assert "pyjwt==2.14.0" in Path("requirements.txt").read_text().splitlines()
