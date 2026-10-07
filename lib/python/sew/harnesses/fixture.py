"""The deterministic fixture harness: run records only, never a suite harness."""

from __future__ import annotations

from .registry import HarnessSpec

SPEC = HarnessSpec(id="fixture", label="fixture")
