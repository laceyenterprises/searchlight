import json
from pathlib import Path
from importlib.metadata import version
import jwt
from app import claims

KEY = "test-key-with-at-least-thirty-two-bytes"


def test_offline_advisory():
    advisory = json.loads(Path(__file__).with_name("advisory.json").read_text())
    assert tuple(map(int, version("PyJWT").split(".")[:2])) >= tuple(advisory["fixed"])


def test_delivered_security_pin():
    requirements = Path("requirements.txt")
    assert requirements.is_file(), "security repair must deliver requirements"
    pins = [line.strip() for line in requirements.read_text().splitlines()]
    assert [pin for pin in pins if pin.startswith("pyjwt==")] == ["pyjwt==2.12.0"]


def test_unknown_critical_extension():
    token = jwt.encode(
        {"sub": "a"},
        KEY,
        algorithm="HS256",
        headers={"crit": ["unrecognized"], "unrecognized": True},
    )
    assert claims(token, KEY) is None


def test_expired_claims():
    token = jwt.encode({"sub": "a", "exp": 1}, KEY, algorithm="HS256")
    assert claims(token, KEY) is None
