"""Session capability checks for tests that execute real containment tools."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Capability:
    available: bool
    reason: str
    expected: bool = True


def probe_containment(platform=None):
    """Capture diagnostics silently; never expose daemon or host details."""
    platform = sys.platform if platform is None else platform
    commands = {
        "seatbelt": (
            [
                "/usr/bin/sandbox-exec",
                "-p",
                "(version 1)(allow default)",
                "/usr/bin/true",
            ],
            platform == "darwin",
            "nested sandbox_apply refused (caller is already sandboxed)",
        ),
        "bubblewrap": (
            ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "/usr/bin/true"],
            platform == "linux",
            "bubblewrap cannot create a namespace",
        ),
        "ps": (["ps", "-A", "-o", "pid="], True, "process inspection denied"),
        "docker": (["docker", "info"], True, "Docker daemon unreachable"),
    }
    capabilities = {}
    for name, (command, expected, refused) in commands.items():
        if not expected:
            capabilities[name] = Capability(
                False, f"{name} requires another platform", False
            )
            continue
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=10, check=False
            )
        except FileNotFoundError:
            capabilities[name] = Capability(False, f"{name} executable not installed")
        except subprocess.TimeoutExpired:
            capabilities[name] = Capability(False, f"{name} capability probe timed out")
        except OSError:
            capabilities[name] = Capability(False, refused)
        else:
            if name == "seatbelt" and "sandbox_apply" not in (result.stderr or ""):
                refused = "sandbox-exec could not apply a nested profile"
            capabilities[name] = Capability(result.returncode == 0, refused)
    return capabilities


def require_capability(capabilities, name):
    capability = capabilities[name]
    if capability.available:
        return
    reason = "containment unavailable: " + capability.reason
    legacy = "SEW_REQUIRE_" + name.upper()
    mandatory = os.environ.get("SEW_REQUIRE_CONTAINMENT_TESTS") == "1"
    if capability.expected and (mandatory or os.environ.get(legacy) == "1"):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)
