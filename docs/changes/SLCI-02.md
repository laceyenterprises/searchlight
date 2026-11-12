# SLCI-02

Standalone main pushes use one Python 3.12 Linux job with existing smoke and
publication gates. Code PRs retain their complete offline clone proof; a
conservative documentation/report allowlist omits it for prose/data-only PRs.
Weekly and manual runs retain all six OS/Python combinations and their separate
concurrency groups. Pip artifact caching extends into the isolated clone.

The cost report in `docs/ci-cost/SLCI-02.md` records every event, coverage
tradeoffs, the local slow-test profile, known scheduled failures and the
measurement-derived 183.47 → 44.32 runner-minute estimate per merge from
SLCI-01 coverage. The next five merges target at most 50 runner-minutes each,
counting all PR attempts plus the main push. This target adds no CI gate.

Current-head CI repair uses the registered Claude Code spec for the existing
broker-provider restriction and refreshes the state-machine source inventory
for Insight's existing status observations. Runtime lifecycle declarations and
smoke promises are unchanged.
