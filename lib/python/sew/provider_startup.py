"""Resolve pinned npx packages before starting a battery, without running servers."""

from collections.abc import Mapping
from pathlib import Path
import os
import re
import subprocess
import time


from .harness import ProviderExposure


PINNED_PACKAGE = re.compile(
    r"(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+@\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?"
)


def prewarm(exposures: Mapping[str, ProviderExposure]) -> list[str]:
    """Download exact packages with npm exec; never pass provider credentials.

    No vendor entrypoint executes. Unpinned npx configs are refused rather
    than silently resolving a different provider version before the battery.
    """
    packages = set()
    for provider, exposure in exposures.items():
        config = exposure.mcp_server_config or {}
        if Path(str(config.get("command", ""))).name != "npx":
            continue
        args = config.get("args", [])
        package = next(
            (arg for arg in args if isinstance(arg, str) and not arg.startswith("-")), ""
        )
        if not PINNED_PACKAGE.fullmatch(package):
            raise ValueError(f"pre-warm requires an exact pinned npx package for {provider}")
        packages.add(package)
    from .backoff import bounded_exponential_delay

    transient = re.compile(
        r"ETIMEDOUT|ESOCKETTIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|"
        r"ENETUNREACH|EHOSTUNREACH|TLS handshake timeout|connection reset|"
        r"timed? out|HTTP(?:/\S+)?\s+5\d\d|E(?:429|5\d\d)|"
        r"\b(?:429|500|502|503|504)\b|service unavailable|bad gateway",
        re.IGNORECASE,
    )
    for package in sorted(packages):
        for attempt in range(3):
            try:
                subprocess.run(
                    [
                        "npm",
                        "exec",
                        "--yes",
                        "--ignore-scripts",
                        f"--package={package}",
                        "--",
                        "node",
                        "-e",
                        "",
                    ],
                    # Public packages need paths/cache settings, never API keys.
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if key
                        in {
                            "PATH",
                            "HOME",
                            "TMPDIR",
                            "TMP",
                            "TEMP",
                            "SystemRoot",
                            "NPM_CONFIG_CACHE",
                            "npm_config_cache",
                        }
                    },
                    check=True,
                    timeout=180,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                )
                break
            except (OSError, subprocess.SubprocessError) as exc:
                stderr = getattr(exc, "stderr", None) or b""
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                diagnostic = stderr or str(exc)
                retryable = isinstance(exc, (subprocess.TimeoutExpired, ConnectionError)) or bool(
                    transient.search(diagnostic)
                )
                if retryable and attempt < 2:
                    time.sleep(
                        bounded_exponential_delay(
                            attempt,
                            base_seconds=1.0,
                            max_seconds=4.0,
                        )
                    )
                    continue
                raise ValueError(
                    f"provider pre-warm failed for {package} after {attempt + 1} attempt(s): "
                    f"{type(exc).__name__}: {diagnostic}"
                ) from exc
    return sorted(packages)
