"""Emit reviewable authoring tickets and receipts; never dispatch workers."""

from pathlib import Path
from uuid import uuid4

from ..catalog import module_root
from ..runner import atomic_write_json
from ..state import default_state_root
from .calibrate import _outside_tree
from .mine import DEFAULT_PACKAGES, mine


def refresh(*, since, packages=DEFAULT_PACKAGES, state_root=None, root=None, miner=None):
    state_root = _outside_tree(default_state_root() if state_root is None else state_root)
    template = (Path(root or module_root()) / "catalogs/gap/AUTHORING.md").read_text()
    record = (miner or mine)(packages, since=since)
    destination = state_root / "gap/refresh" / uuid4().hex
    tickets = []
    for candidate in record["candidates"]:
        candidate_id = candidate["id"]
        receipt = destination / f"{candidate_id}.json"
        prompt_path = destination / f"{candidate_id}-prompt.md"
        markers = candidate["markers"]
        family = (
            "vulnerable-dependency"
            if "security" in markers
            else "silent-default"
            if "default-change" in markers
            else "api-break"
        )
        substitutions = dict(
            candidate_id=candidate_id,
            family=family,
            candidate_receipt=str(receipt),
            source_url=candidate["oracle"]["source_url"],
            event_date=candidate["provenance"]["event_date"],
        )
        prompt = template
        for key, value in substitutions.items():
            prompt = prompt.replace("{" + key + "}", value)
        # The seed template's fixed supply boundary does not replace the requested
        # target-model cutoff; authors must validate both.
        prompt += f"\nRefresh cutoff (exclusive): {since}. Verify target-model eligibility before calibration.\n"
        atomic_write_json(receipt, candidate)
        prompt_path.write_text(prompt)
        tickets.append(
            dict(
                id=candidate_id,
                title=f"Author {candidate_id} ({family})",
                scope=prompt,
                targetRepo="agent-os",
                targetBranch="main",
                expectedCompletionShape="pr",
                dependencies=[],
                taskKind="coding",
                riskClass="medium",
                workerClass="codex",
                promptPath=str(prompt_path),
                candidateReceipt=str(receipt),
            )
        )
    manifest = dict(
        schema_version=1,
        ecosystem="pypi",
        since=since,
        dispatch_policy="emit-only",
        tickets=tickets,
        mining=record,
    )
    path = destination / "tickets.json"
    atomic_write_json(path, manifest)
    return manifest, path
