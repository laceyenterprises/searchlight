"""Installed resources and neutral public commands are usable after extraction."""

import os
import tomllib
from pathlib import Path

from conftest import MODULE_ROOT
from sew import bakeoff_bundle
from sew.catalog import module_root, validate_lighthouse_catalog
from sew.cli import build_parser
from sew.mcp_meter import default_tariffs_path


def test_public_distribution_contract():
    config = tomllib.loads((MODULE_ROOT / "pyproject.toml").read_text())
    assert config["project"]["name"] == "searchlight"
    assert config["project"]["scripts"] == {"sew": "sew.cli:main", "searchlight": "sew.cli:main"}
    assert config["project"]["optional-dependencies"]["agent-os"] == ["agent-os-app-sdk"]
    assert build_parser().prog == "sew"


def test_resources_available():
    assert validate_lighthouse_catalog()
    assert (module_root() / "fixtures/runs").is_dir()
    assert default_tariffs_path().is_file()


def test_portable_credential_metadata_patterns():
    env = {
        "EXAMPLE_AUTH_PATH": "/tmp/login",
        "EXAMPLE_SECRET_FILE": "/tmp/key",
        "EXAMPLE_TOKEN_VAR": "GH_TOKEN",
        "EXAMPLE_AUTH_VIA_BROKER": "1",
        "EXAMPLE_TOKEN_FROM_AMBIENT_GH": "1",
        "EXAMPLE_TOKEN": "real-secret",
        "EXAMPLE_PASSWORD": "1",
        "EXAMPLE_API_KEY": "/tmp/real-key",
    }
    assert set(bakeoff_bundle.known_credentials(env)) == {"real-secret", "1", "/tmp/real-key"}


def test_installed_suite_uses_the_wheel():
    if os.environ.get("SEW_TEST_INSTALLED"):
        import sew.catalog

        assert "site-packages" in str(Path(sew.catalog.__file__))
        assert module_root().name == "_data"
