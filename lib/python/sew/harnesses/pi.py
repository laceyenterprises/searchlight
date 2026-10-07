"""Pi, an open-source harness. Offline fixture driver only (sew.pi_driver)."""

from __future__ import annotations

from .registry import HarnessSpec

# Not live: a live suite records Pi cells not applicable, and its native arm is
# decided by the model profile (fixtures/pi-model-profiles.yaml), none of which
# has native search.
SPEC = HarnessSpec(
    id="pi",
    label="Pi",
    bin_env="SEW_PI_BIN",
    default_bin="pi",
    native_search=False,
    oss=True,
)
