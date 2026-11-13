"""Synthetic migration fence; old direct readers never run after switch/pause."""

import json
from copy import deepcopy

import pytest
import yaml
from sew.credential_migration import switch_provider, pause_provider
from sew.governed_credentials import lease_config, GovernedCredentialError


def test_switch_is_idempotent_and_pause_never_restores_direct_reader(monkeypatch):
    from sew import host

    original = {"other": {"retained": True}}
    candidate = switch_provider(original, credential_ref="search-example")
    assert candidate["other"] == original["other"] and "credential_leases" not in original
    assert switch_provider(candidate, credential_ref="search-example") == candidate
    monkeypatch.setattr(host, "read_config", lambda env: candidate)
    env = {
        "SEW_BRAVE_API_KEY": "synthetic-credential-for-test",
        "SEW_BRAVE_API_KEY_REF": "op://fixture/brave",
    }
    assert lease_config("brave", env).name == "search-example"
    paused = pause_provider(candidate)
    monkeypatch.setattr(host, "read_config", lambda env: paused)
    with pytest.raises(GovernedCredentialError, match="direct reader forbidden") as error:
        lease_config("brave", env)
    assert env["SEW_BRAVE_API_KEY"] not in repr(error.value) + json.dumps(candidate)
    # Non-migrated provider config remains unaffected.
    assert lease_config("parallel", env) is None


def test_migration_refuses_overwrite_and_inline_material():
    with pytest.raises(GovernedCredentialError):
        switch_provider(
            switch_provider({}, credential_ref="search-example"), credential_ref="other"
        )
    marker = "SYNTHETIC/KEY23/secret-842c"
    with pytest.raises(GovernedCredentialError) as error:
        switch_provider({}, credential_ref=marker)
    assert marker not in repr(error.value)


def test_migrated_and_paused_resolver_never_opens_legacy_env(monkeypatch):
    from sew import host
    from sew.providers import CredentialResolver, CredentialUnavailable
    from sew.governed_credentials import PLACEHOLDER, SOURCE

    def forbidden(*args):
        pytest.fail("legacy env file opened")

    monkeypatch.setattr(host, "credential_environment", forbidden)
    candidate = switch_provider({}, credential_ref="search-example")
    monkeypatch.setattr(host, "read_config", lambda env: candidate)
    assert CredentialResolver(environ={}).resolve("brave") == (PLACEHOLDER, SOURCE)
    candidate = pause_provider(candidate)
    with pytest.raises(CredentialUnavailable, match="direct reader forbidden"):
        CredentialResolver(environ={}).resolve("brave")


def test_switch_accepts_matching_shorthand_but_refuses_conflicting_lease():
    original = {"credential_leases": {"brave": "search-example"}}
    candidate = switch_provider(original, credential_ref="search-example")
    assert candidate["credential_leases"]["brave"] == {"name": "search-example"}
    assert original["credential_leases"]["brave"] == "search-example"
    with pytest.raises(GovernedCredentialError, match="requires reconciliation"):
        switch_provider(original, credential_ref="other")


@pytest.mark.parametrize("leases", [None, {}, {"tavily": "other-provider"}])
@pytest.mark.parametrize("migrations", [None, {}, {"tavily": "broker-only"}])
def test_projection_handles_null_sections_and_preserves_other_providers(leases, migrations):
    original = {"credential_leases": leases, "credential_migrations": migrations}
    snapshot = deepcopy(original)
    switched = switch_provider(original, credential_ref="search-example")
    paused = pause_provider(switched)
    # Pause must also work on an uninitialized config during rollback.
    assert pause_provider(original) == paused
    assert paused["credential_leases"] == (leases or {})
    assert paused["credential_migrations"] == {**(migrations or {}), "brave": "broker-only"}
    assert original == snapshot
    assert switch_provider(switched, credential_ref="search-example") == switched


@pytest.mark.parametrize("section", ["credential_leases", "credential_migrations"])
@pytest.mark.parametrize("value", [False, "", [], "invalid"])
def test_projection_refuses_malformed_sections(section, value):
    config = {section: value}
    with pytest.raises(GovernedCredentialError, match="must be a mapping"):
        switch_provider(config, credential_ref="search-example")
    with pytest.raises(GovernedCredentialError, match="must be a mapping"):
        pause_provider(config)


@pytest.mark.parametrize("null_value", ["", " null"])
def test_yaml_null_sections_allow_unmigrated_resolution_and_fenced_pause(
    tmp_path, null_value
):
    from sew.providers import CredentialResolver, CredentialUnavailable

    path = tmp_path / "sew.yaml"
    path.write_text(
        f"credential_leases:{null_value}\ncredential_migrations:{null_value}\n"
    )
    env = {
        "SEW_CONFIG": str(path),
        "SEW_ENV_FILE": str(tmp_path / "absent.env"),
        "SEW_BRAVE_API_KEY": "synthetic-direct",
        "SEW_PARALLEL_WEB_API_KEY": "synthetic-other",
    }
    assert lease_config("brave", env) is None
    assert CredentialResolver(environ=env).resolve("brave") == (
        "synthetic-direct", "SEW_BRAVE_API_KEY"
    )
    assert CredentialResolver(environ=env).resolve("parallel-web") == (
        "synthetic-other", "SEW_PARALLEL_WEB_API_KEY"
    )
    paused = pause_provider(yaml.safe_load(path.read_text()))
    path.write_text(yaml.safe_dump(paused))
    with pytest.raises(CredentialUnavailable, match="direct reader forbidden"):
        CredentialResolver(environ=env).resolve("brave")


@pytest.mark.parametrize("value", [False, "", [], "invalid"])
def test_lease_config_refuses_malformed_migrations(monkeypatch, value):
    from sew import host

    monkeypatch.setattr(host, "read_config", lambda env: {"credential_migrations": value})
    with pytest.raises(GovernedCredentialError, match="invalid credential migration config"):
        lease_config("brave", {})
