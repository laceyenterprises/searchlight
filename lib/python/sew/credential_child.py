"""Approved credential child for governed provider calls (KEY-21).

The Agent OS broker launches this module with a fresh environment and the
provider key on a private pipe FD (``AGENT_OS_CREDENTIAL_FD``) or one
allowlisted variable. It reads one request file written by the Searchlight
worker, refuses anything except the governed provider's fixed origin, paths
and auth header, substitutes the key for the placeholder in that header only,
performs the request and writes a key-free response file. stdout/stderr are
discarded by the launcher; nothing here prints. Exit 0 means a response file
was written (including provider HTTP errors); exit 2 means refused.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .governed_credentials import (
    GOVERNED_PROVIDERS,
    MAX_RESPONSE_BYTES,
    PLACEHOLDER,
    write_private_json,
)

REDACTED = "[redacted]"
METHODS = frozenset({"GET", "POST"})
MAX_REQUEST_BYTES = 1024 * 1024


class Refused(Exception):
    """The request is outside the governed provider contract."""


def read_delivered(environ: Mapping[str, str]) -> str:
    fd = environ.get("AGENT_OS_CREDENTIAL_FD")
    if fd is not None:
        with os.fdopen(int(fd), "rb") as stream:
            value = stream.read(65537).decode("utf-8")
    else:
        name = environ.get("AGENT_OS_CREDENTIAL_AUDIENCE", "")
        value = environ.get(name, "") if name else ""
    if not value or len(value) > 65536:
        raise Refused("no delivered credential")
    return value


def _validated(document: Any, provider_id: str) -> dict[str, Any]:
    governed = GOVERNED_PROVIDERS.get(provider_id)
    if governed is None or not isinstance(document, dict):
        raise Refused("provider")
    if set(document) != {"provider", "method", "url", "headers", "json_body", "timeout_seconds"}:
        raise Refused("shape")
    if document["provider"] != provider_id or document["method"] not in METHODS:
        raise Refused("provider")
    parts = urlsplit(str(document["url"]))
    origin = f"{parts.scheme}://{parts.netloc}"
    if (
        origin != governed.origin
        or parts.path not in governed.paths
        or parts.username
        or parts.password
        or parts.fragment
    ):
        raise Refused("destination")
    headers = document["headers"]
    if not isinstance(headers, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()
    ):
        raise Refused("headers")
    carriers = [k for k, v in headers.items() if PLACEHOLDER in v]
    # The key goes to exactly one header, whole-value, and nowhere else.
    if carriers != [governed.header] or headers[governed.header] != PLACEHOLDER:
        raise Refused("headers")
    if PLACEHOLDER in json.dumps(document["json_body"]) or PLACEHOLDER in document["url"]:
        raise Refused("placement")
    timeout = document["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise Refused("timeout")
    return document


def _scrub(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, REDACTED)
    if isinstance(value, list):
        return [_scrub(item, secret) for item in value]
    if isinstance(value, dict):
        return {_scrub(k, secret): _scrub(v, secret) for k, v in value.items()}
    return value


def run(
    provider_id: str,
    request_path: Path,
    response_path: Path,
    *,
    environ: Mapping[str, str],
    transport: Any = None,
) -> int:
    from .providers import UrllibTransport, http_error_body, is_timeout_error

    secret = None
    try:
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise Refused("size")
        document = _validated(json.loads(request_path.read_text(encoding="utf-8")), provider_id)
        secret = read_delivered(environ)
    except (Refused, OSError, ValueError):
        write_private_json(response_path, {"error": "refused"})
        return 2
    headers = dict(document["headers"])
    headers[GOVERNED_PROVIDERS[provider_id].header] = secret
    transport = transport or UrllibTransport()
    try:
        response = transport.request(
            document["method"],
            document["url"],
            headers=headers,
            json_body=document["json_body"],
            timeout_seconds=float(document["timeout_seconds"]),
        )
        reply: dict[str, Any] = {
            "status_code": int(response.status_code),
            "body": response.body,
            "headers": dict(response.headers or {}),
        }
    except urllib.error.HTTPError as exc:
        reply = {"status_code": exc.code, "body": http_error_body(exc), "headers": {}}
    except (TimeoutError, socket.timeout):
        reply = {"error": "timeout"}
    except OSError as exc:
        reply = {"error": "timeout"} if is_timeout_error(exc) else {"error": "transport"}
    finally:
        headers = None
    # Defense in depth: a provider that echoes the key never leaks it onward.
    encoded = json.dumps(_scrub(reply, secret))
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({"error": "transport"})
    write_private_json(response_path, json.loads(encoded))
    return 0


def main(argv: list[str] | None = None, *, environ=None, transport=None) -> int:
    parser = argparse.ArgumentParser(prog="sew.credential_child", add_help=False)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    return run(
        args.provider,
        args.request,
        args.response,
        environ=os.environ if environ is None else environ,
        transport=transport,
    )


if __name__ == "__main__":
    sys.exit(main())
