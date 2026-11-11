"""Grok native fleet cells; search arms stay unavailable until isolation is proven."""

from .registry import HarnessSpec

UNAVAILABLE = "unavailable: Grok 1.0.70 search-arm tool isolation is not qualified"


def arm_spawn(*args, **kwargs):
    from sew.schema import SchemaError

    raise SchemaError(UNAVAILABLE)


def fleet_cells(*, opt_in=False, timeout=240):
    """Optional host adapter; importing Searchlight needs no fleet installation."""
    from cwp_dispatch.grok_smoke import KINDS, ROLES, run_cell

    return [
        run_cell(role, kind, opt_in=opt_in, timeout=timeout) for role in ROLES for kind in KINDS
    ]


SPEC = HarnessSpec(
    id="grok",
    label="Grok Build (fleet smoke; search arms unavailable)",
    arm_spawn=arm_spawn,
    minimum_version="1.0.70",
)
