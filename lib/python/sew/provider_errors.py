"""Vendored for standalone SEW from agent_os_core/provider_errors (SWX-02)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "FALLBACK_SCRUB_CONTRACT",
    "SMOKE_SECRET_PATTERNS",
    "build_scrub_state",
    "header_patterns",
    "scrub_provider_error_text",
    "scrub_with_state",
    "token_prefix_patterns",
]

_TOKEN_VALUE_CHARS = r"[A-Za-z0-9_\-]"
_BEARER_PATTERN = re.compile(r"Bearer\s+([A-Za-z0-9_\-.]{20,})")

# The checked-in HRR-09 secret-scrub vocabulary. `cwp_dispatch.provider_errors`
# parses the same vocabulary out of the build-pack SPEC at runtime and falls
# back to this table when that parse fails.
FALLBACK_SCRUB_CONTRACT: dict[str, list[str]] = {
    "token_prefixes": [
        "sk-ant-admin-",
        "github_pat_",
        "sk-proj-",
        "sk-ant-",
        "ghp_",
        "gho_",
        "ghs_",
        "ghr_",
        "ghu_",
        "AKIA",
        "ASIA",
        "sk-vk-",
        "sk-",
    ],
    "headers": [
        "Authorization:",
        "X-Api-Key:",
        "Api-Key:",
        "X-API-KEY:",
        "Cookie:",
        "Set-Cookie:",
        "Proxy-Authorization:",
    ],
    "body_keys": [
        "refresh_token",
        "access_token",
        "id_token",
        "api_key",
        "apikey",
        "client_secret",
        "password",
        "passwd",
        "secret",
        "private_key",
        "session_token",
        "x_api_key",
    ],
    "bearer_rules": [
        "Bearer-opaque",
    ],
}

# Credential/PII shapes refused in browse smoke fixtures. Relocated from the
# private `cwp_dispatch.cli_browse._SMOKE_SECRET_PATTERNS`, which still aliases
# this tuple.
SMOKE_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bearer-token",
        re.compile(r"\bBearer\s+[A-Za-z0-9_\-./=+]{16,}", re.ASCII | re.IGNORECASE),
    ),
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.ASCII | re.IGNORECASE),
    ),
    (
        "secret-field",
        re.compile(
            r"""(?x)
            ["']?
            (?:password|passwd|pwd|client[_-]?secret|secret|api[_-]?key|api[_-]?token|
               access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?token)
            ["']?\s*[:=]\s*["']?[A-Za-z0-9_\-./=+]{8,}
            """,
            re.ASCII | re.IGNORECASE,
        ),
    ),
    (
        "cookie",
        re.compile(
            r"""["']?(?:cookie|cookies|set-cookie)["']?\s*[:=]\s*["']?[^"'\s]{8,}""",
            re.ASCII | re.IGNORECASE,
        ),
    ),
    (
        "email-address",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.ASCII | re.IGNORECASE),
    ),
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b", re.ASCII),
    ),
)


_FALLBACK_SCRUB_STATE: dict[str, Any] | None = None


def token_prefix_patterns(prefixes: Iterable[str]) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for prefix in sorted(prefixes, key=len, reverse=True):
        patterns.append(
            (
                re.compile(rf"(?<![A-Za-z0-9_<:\-]){re.escape(prefix)}{_TOKEN_VALUE_CHARS}{{10,}}"),
                prefix,
            )
        )
    return patterns


def header_patterns(headers: Iterable[str]) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for header in headers:
        patterns.append(
            (
                re.compile(rf"(?im)^(?P<header>{re.escape(header)})\s*.*$"),
                header,
            )
        )
    return patterns


def build_scrub_state(contract: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Compile one scrub vocabulary into the state `scrub_with_state` consumes."""
    return {
        "contract": contract,
        "token_patterns": token_prefix_patterns(contract["token_prefixes"]),
        "header_patterns": header_patterns(contract["headers"]),
        "body_keys": tuple(contract["body_keys"]),
    }


def _scrub_json_body_keys(text: str, body_keys: Iterable[str]) -> str:
    for key in body_keys:
        pattern = re.compile(rf'("{re.escape(key)}"\s*:\s*)"(?:[^"\\]|\\.)*"')
        text = pattern.sub(rf'\1"<redacted:{key}>"', text)
    return text


def _scrub_form_body_keys(text: str, body_keys: Iterable[str]) -> str:
    for key in body_keys:
        pattern = re.compile(
            rf"(?P<prefix>(?:^|[?&\s]))(?P<key>{re.escape(key)})=(?P<value>[^&\s]+)"
        )
        text = pattern.sub(
            lambda match, key=key: f"{match.group('prefix')}{match.group('key')}=<redacted:{key}>",
            text,
        )
    return text


def scrub_with_state(text: str, state: Mapping[str, Any]) -> str:
    """Redact provider error text using an already-compiled scrub state."""
    scrubbed = text
    for pattern, kind in state["token_patterns"]:
        scrubbed = pattern.sub(f"<redacted:{kind}>", scrubbed)
    for pattern, _ in state["header_patterns"]:
        scrubbed = pattern.sub(
            lambda match: (
                f"{match.group('header')} <redacted:{match.group('header').rstrip(':')}-header>"
            ),
            scrubbed,
        )
    scrubbed = _scrub_json_body_keys(scrubbed, state["body_keys"])
    scrubbed = _scrub_form_body_keys(scrubbed, state["body_keys"])
    return _BEARER_PATTERN.sub("Bearer <redacted:Bearer-opaque>", scrubbed)


def scrub_provider_error_text(text: str) -> str:
    """Redact provider error text using the checked-in HRR-09 vocabulary.

    `cwp_dispatch.provider_errors.scrub_provider_error_text` passes its
    SPEC-derived state to `scrub_with_state` instead; the two vocabularies are
    pinned equal by test.
    """
    global _FALLBACK_SCRUB_STATE
    if _FALLBACK_SCRUB_STATE is None:
        _FALLBACK_SCRUB_STATE = build_scrub_state(FALLBACK_SCRUB_CONTRACT)
    return scrub_with_state(text, _FALLBACK_SCRUB_STATE)
