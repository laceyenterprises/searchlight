"""Judge selection for GAP brief grading.

Briefs are graded by Claude Code (primary, decides the verdict) and codex (second
judge, measures agreement). ``SEW_GAP_JUDGES=claude-code`` grades with the primary
only, for example while codex quota is exhausted; the primary still decides the
verdict, so a later regrade with both judges adds the agreement measurement
without changing pass/fail.
"""
from __future__ import annotations

import os

DEFAULT_BRIEF_JUDGES = ("claude-code", "codex")
ENV = "SEW_GAP_JUDGES"


def brief_judge_harnesses(environ=None) -> tuple[str, ...]:
    raw = (os.environ if environ is None else environ).get(ENV, "")
    if not raw.strip():
        return DEFAULT_BRIEF_JUDGES
    names = tuple(name.strip() for name in raw.split(",") if name.strip())
    if names not in (DEFAULT_BRIEF_JUDGES, DEFAULT_BRIEF_JUDGES[:1]):
        raise ValueError(f"{ENV} must be 'claude-code,codex' (default) or 'claude-code' (primary only)")
    return names


def default_brief_judges(harness_auth):
    from ..judge import Judge
    from ..judge_transport import HarnessJudgeTransport

    return [Judge(h, HarnessJudgeTransport(h, harness_auth=harness_auth)) for h in brief_judge_harnesses()]
