# Standalone reproduction

Run from a source checkout installed with `python3 -m pip install ".[test]"`.
Use your own harness account login and provider keys as described in the
[repository README](../../README.md). Live runs consume quota. Configure an
outside-tree MCP mapping using environment references; never embed credentials.
The recorded provider package versions are in [the WSB report](../2026-09-29-web-search-bakeoff/REPORT.md#what-ran)
and the GAP pins are enforced by the workbench. Use an outside-tree state root.

Catalog identity is SHA-256 of `catalogs/gap/tasks.yaml` file bytes:

- Historical battery: `13f44a7246369368a87ca959a25986e505c678565d0090ffd35869355829b1bc`
- Shipped catalog: `29f8fd1731cd0155f2a993688d8f1dfac7ad6ef1f6effc549185af026ad24443`

```sh
shasum -a 256 catalogs/gap/tasks.yaml
python3 scripts/check_reports.py
```

The shipped catalog differs from the historical catalog. Historical hashes are
identities, not downloadable private snapshots. These commands reproduce the
experiment procedure on the shipped corpus; they cannot exactly replay the old
numbers without the original graded deliverables and captured sources. Sources,
provider indexes and models also drift. Report rendering, in contrast, is exact
and offline: `python3 scripts/check_reports.py --write`.

The historical battery ran briefs only. Restrict calibration to the ten
brief tasks so the later code corpus is not accidentally included. Repeat the
commands for `claude-code` with `claude-opus-5-5`; Codex used `gpt-6.1-sol`.
Fresh calibration can admit different tasks. The committed calibration.json is
for reading only; the runner requires its own live calibration and captured sources.
`gap run` without `--task` selects only admitted tasks in the calibration for
that state root, harness and model. The `gap calibrate` command below replaces
that calibration with these ten briefs, including in a reused state root. Run it
immediately before the battery; do not substitute an older calibration that
may include code tasks. Passing all ten briefs to `gap run` would refuse any
that fresh calibration rejected.

```sh
export SEW_MODE=standalone SEW_HARNESS_LIVE=1
sew gap list
sew gap calibrate --harness codex --model gpt-6.1-sol --reps 5 \
  --harness-auth account --state-root "$HOME/searchlight-state" \
  --task gap-brief-org-migration-2026 \
  --task gap-brief-models-production-2026 \
  --task gap-brief-classroom-term-2026 \
  --task gap-brief-spark-editing-2026 \
  --task gap-brief-cli-trust-2026 \
  --task gap-brief-tarfile-upload-2026 \
  --task gap-brief-python-metadata-2026 \
  --task gap-brief-python-security-march-2026 \
  --task gap-brief-npm-token-scope-2026 \
  --task gap-brief-billing-reporting-2026
sew gap run --harness codex --model gpt-6.1-sol --reps 3 \
  --harness-auth account --state-root "$HOME/searchlight-state" \
  --run-root "$HOME/searchlight-gap-codex" \
  --provider-mcp-config "$HOME/searchlight-mcp.yaml" --prewarm-providers \
  --arm floor --arm ceiling --arm native --arm brave --arm tavily \
  --arm exa --arm parallel-web --arm firecrawl --arm perplexity
sew gap report "$HOME/searchlight-gap-codex"
# Resume only genuinely unavailable cells after fixing provider availability:
# Repeat the gap run command with --resume --rerun-unavailable.
```

Grading happens during calibration and execution; the current grader incorporates
GAPJUDGE-01 and GAPRULE-01. The historical regrade used saved judge labels rather
than new answers. Do not interpret a fresh run as that historical regrade.
See [methodology](methodology.md) and [GAP runbook](../../RUNBOOK-gap.md).
