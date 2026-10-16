#!/usr/bin/env python3
"""Installed SMV child, or explicit sibling-checkout invocation for workers."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib/python"))


def run():
    if "SMV_MODE" not in os.environ:
        parser = argparse.ArgumentParser()
        parser.add_argument("--agent-os-root", type=Path, required=True)
        args = parser.parse_args()
        sys.path.insert(0, str(args.agent_os_root.resolve() / "modules/worker-pool/lib/python"))
        from cwp_dispatch.process_group_runner import termination_cleanup
        from cwp_dispatch.smv.contracts import declaration
        from cwp_dispatch.smv.smoke import invoke

        doc = declaration(json.loads((ROOT / "post-merge-verify.json").read_text()))
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        with termination_cleanup():
            code, report = invoke(
                args.agent_os_root.resolve(),
                doc,
                mode="sandbox",
                commit=commit,
                command=["python3", str(Path(__file__).resolve())],
            )
        for step in report["steps"]:
            print(
                f"{step['verdict'].upper()} search-evaluation-workbench/{step['id']} "
                f"promise={step['promise']} edges={','.join(step['edges'])}"
            )
        print(json.dumps(report))
        return code
    from cwp_dispatch.process_group_runner import termination_cleanup
    from sew.smv import main

    def overdue(*_):
        raise TimeoutError("SMV-18 deadline, unwind before outer teardown")

    with termination_cleanup():
        signal.signal(signal.SIGALRM, overdue)
        signal.setitimer(signal.ITIMER_REAL, 150)
        try:
            return main()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    raise SystemExit(run())
