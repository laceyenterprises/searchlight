"""Harness registry (OHM-01). See :mod:`sew.harnesses.registry`.

Registration order is the order of CLI choices and doctor lines.
"""

from __future__ import annotations

from . import claude_code, codex, fixture, pi
from .registry import (
    HarnessSpec,
    field_map,
    find,
    get,
    ids,
    protocols,
    register,
    specs,
    unregister,
)

for _module in (claude_code, codex, pi, fixture):
    register(_module.SPEC)
del _module

__all__ = [
    "HarnessSpec",
    "field_map",
    "find",
    "get",
    "ids",
    "protocols",
    "register",
    "specs",
    "unregister",
]
