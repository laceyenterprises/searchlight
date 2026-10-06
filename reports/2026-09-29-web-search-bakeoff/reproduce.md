# Standalone reproduction

Run from a source checkout installed with `python3 -m pip install ".[test]"`.
Use your own harness account login and provider keys as described in the
[repository README](../../README.md). Live runs consume quota. Configure an
outside-tree MCP mapping using environment references; never embed credentials.
The recorded provider package versions are in [REPORT.md](REPORT.md) for WSB
and the GAP pins are enforced by the workbench. Use an outside-tree state root.

Catalog identity is SHA-256 of `catalogs/production/tasks.yaml` file bytes:

- Historical initial: `144c4a41b959996f607dcd7b90c1aee091f4771efc6e1ebeb37c11d40f8f9d1c`
- Historical regrade: `f53e4a536efc2a1c66c532754d156e771c53da026f73eadfcb9d6650b3f63442`
- Shipped catalog: `39b42ae3428c37e0befce489b14cf03342c638c21e27c0341ba027f49d0be78b`

```sh
shasum -a 256 catalogs/production/tasks.yaml
python3 scripts/check_reports.py
```

The shipped catalog differs from the historical catalog. Historical hashes are
identities, not downloadable private snapshots. These commands reproduce the
experiment procedure on the shipped corpus; they cannot exactly replay the old
numbers without the original graded deliverables and captured sources. Sources,
provider indexes and models also drift. Report rendering, in contrast, is exact
and offline: `python3 scripts/check_reports.py --write`.

Configure Claude Code to use `claude-opus-5-5[1m]` if available; the WSB
runner uses the configured default model. The included suite covers all production
tasks, all eight arms and three repetitions (the small wsb-trial suite does not).
The example run caps are explicit limits for a new run, not recovered historical
battery-wide limits. Increase them only after reviewing the projected spend.
The suite's `budgets` are spend ceilings: a `--max-*` spend cap can lower them, not
raise them, and the included suite sets them to the caps below. The run's wall clock
is `--max-wall-clock-seconds` as given. The run prints the limits that applied as
`budget_limits`. Elapsed time carries across resumes, so after a
`wall_clock_budget_exhausted` stop, rerun the same command with a larger
`--max-wall-clock-seconds` to continue.

```sh
export SEW_MODE=standalone SEW_HARNESS_LIVE=1
sew run --suite "$PWD/reports/2026-09-29-web-search-bakeoff" --mode live \
  --harness-auth account --provider-mcp-config "$HOME/searchlight-mcp.yaml" \
  --state-root "$HOME/searchlight-state" \
  --max-provider-calls 10000 --max-provider-result-chars 50000000 \
  --max-total-tokens 50000000 --max-wall-clock-seconds 172800
# Use the suite directory printed by the run command:
sew bakeoff grade --judge-harness claude-code --harness-auth account \
  "$HOME/searchlight-state/SUITE_DIRECTORY"
sew bakeoff report "$HOME/searchlight-state/SUITE_DIRECTORY"
# To replay your own stored deliverables with the current grader:
sew bakeoff grade --regrade --judge-harness claude-code --harness-auth account \
  "$HOME/searchlight-state/SUITE_DIRECTORY"
```

See [methodology](methodology.md) for the difference between report-command cost
and the findings' manually calculated lower bounds, and missing measured coverage.
