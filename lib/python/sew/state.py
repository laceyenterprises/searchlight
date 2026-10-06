"""State-root resolution through the selected workbench host."""

from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
from .host import get_host

STATE_ROOT_ENV = "SEW_STATE_ROOT"


def default_state_root(env: Mapping[str, str] | None = None) -> Path:
    return get_host(env).state_root()
