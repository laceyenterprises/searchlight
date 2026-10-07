"""Per-spawn harness credentials from the worker-pool OAuth broker readers."""

from __future__ import annotations

import errno
import hashlib
import json
import math
from datetime import datetime
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Mapping


class BrokerClaudeEnv(dict[str, str]):
    """Child environment with non-secret broker receipt metadata."""

    def __init__(self, token: str, fingerprint: str, expires_at: str) -> None:
        super().__init__(ANTHROPIC_AUTH_TOKEN=token)
        self.metadata = {
            "source": "broker",
            "fingerprint": fingerprint,
            "expires_at": expires_at,
        }


class BrokerAuthError(RuntimeError):
    """A broker credential was unavailable; the harness must not spawn."""


class ReceiptRefused(ValueError):
    """A non-secret receipt validation reason safe to surface to the operator."""


CODEX_MIN_REMAINING_LIFETIME_S = 120
CODEX_CLOCK_SKEW_TOLERANCE_S = 60


_TRANSIENT_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.EINTR,
        errno.EIO,
        errno.ETIMEDOUT,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ENETUNREACH,
    }
)
_TRANSIENT_STDERR = re.compile(
    rb"timed?\s*out|timeout|temporar(?:y|ily)|connection (?:reset|refused|aborted)|"
    rb"network is unreachable|(?:tls|ssl) handshake (?:failed|timed out|timeout)|"
    rb"http (?:error |status )?5\d\d|status\s*[:=]\s*5\d\d|"
    rb"(?:http (?:error |status )?|status\s*[:=]\s*)429",
    re.I,
)
_TRANSIENT_OUTCOMES = frozenset(
    {
        b"oauth-broker-unreachable",
        b"oauth-broker-gate-saturated",
        b"oauth-broker-bad-response",
    }
)


def _service_error(exc: BaseException) -> BaseException:
    """Recover SDK-wrapped transport errors for retry and scrubbed diagnostics."""
    seen = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        underlying = exc.__cause__ or exc.__context__
        if underlying is None or id(underlying) in seen:
            break
        exc = underlying
    return exc


def _stderr(exc: BaseException) -> bytes:
    exc = _service_error(exc)
    value = getattr(exc, "stderr", None)
    if value is None:
        return b""
    return value.encode(errors="replace") if isinstance(value, str) else value


def _transient(exc: BaseException) -> bool:
    exc = _service_error(exc)
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    if isinstance(exc, OSError):
        return exc.errno in _TRANSIENT_ERRNOS
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = _stderr(exc)
        outcome = re.search(rb"\boutcome=([a-z0-9_-]+)", stderr, re.I)
        if outcome:
            kind = outcome.group(1).lower()
            return kind in _TRANSIENT_OUTCOMES
        return (
            exc.returncode == 75
            or bool(re.search(rb"\bhttp=(?:000|429|5\d\d)\b", stderr))
            or bool(_TRANSIENT_STDERR.search(stderr))
        )
    return False


def _auth_failure(name: str, log_dir: Path, exc: BaseException) -> BrokerAuthError:
    from .live_harness import scrub_text

    raw = _stderr(exc) or str(exc).encode(errors="replace")
    diagnostic = scrub_text(raw.decode(errors="replace")).encode()
    path = log_dir / f"{name}-broker-auth-stderr.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(diagnostic)
    reason = ""
    if isinstance(exc, ReceiptRefused):
        reason = f"; {exc}"
    return BrokerAuthError(f"{name} broker auth failed{reason}; diagnostic: {path}")


def auth_source(requested: str | None = None, environ: Mapping[str, str] | None = None, *, model_id: str | None = None) -> str:
    from .host import get_host

    if requested not in {None, "broker", "account", "litellm"}:
        raise ValueError("harness auth must be broker, account or litellm")
    if (model_id and model_id.startswith("litellm/")) or requested == "litellm":
        return "litellm"
    if requested == "account":
        return "account"
    host = get_host(environ)
    if host.mode == "standalone":
        if requested == "broker":
            raise BrokerAuthError(host.unavailable_message("broker auth"))
        return "account"
    return (
        "broker"
        if requested == "broker"
        else getattr(host, "harness_auth_source", lambda: "broker")()
    )


def _broker_credentials(harness: str, log_dir: Path, environ: Mapping[str, str] | None):
    from .host import get_host

    host = get_host(environ)
    if host.mode == "standalone":
        raise BrokerAuthError(host.unavailable_message("broker auth"))
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    from .backoff import bounded_exponential_delay

    for attempt in range(3):
        try:
            return host.harness_auth(harness)
        except Exception as exc:
            if not _transient(exc) or attempt == 2:
                raise _auth_failure(harness, log_dir, exc) from exc
            time.sleep(bounded_exponential_delay(attempt, base_seconds=0.5, max_seconds=2))
    raise AssertionError("unreachable")


def claude_code_env(log_dir: Path, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    receipt = _broker_credentials("claude-code", log_dir, environ)
    try:
        token = receipt["env"]["ANTHROPIC_AUTH_TOKEN"]
        if not isinstance(token, str) or not token:
            raise ValueError("empty token")
        return BrokerClaudeEnv(token, receipt["fingerprint"], receipt["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _auth_failure("claude-code", log_dir, exc) from exc


def codex_home_auth(
    codex_home: Path, log_dir: Path, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    from .host import get_host

    host = get_host(environ)
    if host.mode == "standalone":
        raise BrokerAuthError(host.unavailable_message("broker auth"))
    auth_path = codex_home / "auth.json"
    try:
        receipt = _broker_credentials("codex", log_dir, environ)
        auth = receipt["auth"]
        tokens = auth["tokens"]
        if tokens["refresh_token"] != "agent-os-per-worker-placeholder-no-rotate":
            raise ValueError("unsafe refresh token")
        access = tokens["access_token"]
        expires_at = str(tokens["expires_at"])
        try:
            expiry = float(expires_at)
        except (ValueError, TypeError) as exc:
            raise ReceiptRefused("codex broker expires_at must be numeric") from exc
        now = time.time()
        if not math.isfinite(expiry) or expiry <= now:
            raise ReceiptRefused("codex broker receipt expired; refusing harness spawn")
        if expiry <= now + CODEX_MIN_REMAINING_LIFETIME_S:
            raise ReceiptRefused("codex broker receipt expires within 120s; refusing harness spawn")
        stamp = auth.get("last_refresh")
        if not isinstance(stamp, str) or not stamp:
            raise ReceiptRefused(
                "codex broker receipt missing last_refresh; refusing harness spawn"
            )
        try:
            refreshed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReceiptRefused("codex broker last_refresh is not RFC3339") from exc
        if refreshed.tzinfo is None:
            raise ReceiptRefused("codex broker last_refresh must include timezone")
        refreshed_at = refreshed.timestamp()
        if refreshed_at > now + CODEX_CLOCK_SKEW_TOLERANCE_S:
            raise ReceiptRefused("codex broker last_refresh is in the future")
        if "fetched_at" in tokens:
            try:
                fetched_at = float(tokens["fetched_at"])
            except (ValueError, TypeError) as exc:
                raise ReceiptRefused("codex broker fetched_at must be numeric") from exc
            if not math.isfinite(fetched_at) or abs(refreshed_at - fetched_at) >= 1:
                raise ReceiptRefused("codex broker last_refresh does not match fetched_at")
        if not isinstance(access, str) or not access:
            raise ValueError("empty access token")
        codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(auth_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(auth, stream)
        return {
            "source": "broker",
            "fingerprint": hashlib.sha256(access.encode()).hexdigest()[:12],
            "expires_at": expires_at,
        }
    except BrokerAuthError:
        auth_path.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        auth_path.unlink(missing_ok=True)
        raise _auth_failure("codex", log_dir, exc) from exc
