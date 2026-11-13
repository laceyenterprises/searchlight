"""Pure Brave cohort projection; operator checkpoints own publication authority."""

from copy import deepcopy

from .governed_credentials import IDENTIFIER, GovernedCredentialError


def _section(config, name):
    section = config.get(name)
    if section is None:
        section = config[name] = {}
    if not isinstance(section, dict):
        raise GovernedCredentialError(f"migration: {name} must be a mapping")
    return section


def switch_provider(config, *, credential_ref):
    if not isinstance(credential_ref, str) or not IDENTIFIER.fullmatch(credential_ref):
        raise GovernedCredentialError("migration: invalid credential reference")
    result = deepcopy(config)
    leases = _section(result, "credential_leases")
    existing = leases.get("brave")
    if isinstance(existing, str):
        existing = {"name": existing}
    if existing is not None and existing != {"name": credential_ref}:
        raise GovernedCredentialError("migration: existing lease requires reconciliation")
    leases["brave"] = {"name": credential_ref}
    _section(result, "credential_migrations")["brave"] = "broker-only"
    return result


def pause_provider(config):
    """Rollback removes delivery but retains the fence against direct readers."""
    result = deepcopy(config)
    _section(result, "credential_leases").pop("brave", None)
    _section(result, "credential_migrations")["brave"] = "broker-only"
    return result
