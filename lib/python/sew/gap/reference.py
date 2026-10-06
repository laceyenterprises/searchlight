"""Shared GAP task resolution and catalog-bound reference prompts."""

from copy import deepcopy
from pathlib import Path

from ..catalog import module_root
from ..schema import SchemaError
from .catalog import load_gap_tasks


def resolve_gap_task(config):
    """Resolve shared GAP prompts and budgets without changing the arm.

    Reuse the validated catalog only while its bytes and asset metadata agree.
    """
    root = Path(config.gap_module_root or module_root()).resolve()
    base = root / "catalogs" / "gap"
    # Read catalog bytes to catch even same-size edits with restored mtimes.
    # Stat assets only: hidden/verifier contents must never be opened here.
    try:
        assets = []
        for path in sorted(base.rglob("*")):
            stat = path.lstat()
            assets.append(
                (
                    str(path.relative_to(base)),
                    stat.st_mode,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    str(path.resolve()) if path.is_symlink() else None,
                )
            )
        signature = (base.joinpath("tasks.yaml").read_bytes(), tuple(assets))
    except OSError as exc:
        raise SchemaError(f"cannot load GAP catalog: {base}: {exc}") from exc
    cached = getattr(config, "_gap_reference_catalog", None)
    if cached is None or cached[0] != signature:
        tasks = load_gap_tasks(root)
        # Config is frozen; this private memo does not change its public fields.
        object.__setattr__(config, "_gap_reference_catalog", (signature, tasks))
    else:
        tasks = cached[1]
    task = deepcopy(tasks.get(config.task_id))
    if task is None:
        raise SchemaError("GAP evaluation requires a GAP catalog task")
    if config.workspace_profile != (task["task_type"] == "code"):
        raise SchemaError("GAP evaluation profile must match GAP task type")
    if config.prompt_text is not None and config.prompt_text != task["prompt"]:
        raise SchemaError("GAP evaluation prompt must match the catalog")
    return root, task


def reference_prompt(arm, task):
    """Preserve excerpt bytes, including trailing whitespace."""
    if arm == "floor":
        return task["prompt"]
    if arm != "ceiling":
        raise SchemaError("unknown reference arm")
    oracle = task["oracle"]
    return (
        task["prompt"]
        + "\n\nReference material:\n"
        + "Source URL: "
        + oracle["source_url"]
        + "\n"
        + "Retrieved date: "
        + oracle["retrieved_at"]
        + "\n\n"
        + oracle["excerpt"]
    )
