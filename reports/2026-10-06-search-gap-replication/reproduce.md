# Standalone reproduction

Run from a source checkout installed with `python3 -m pip install ".[test]"`, with your own harness account
login and provider keys as described in the [repository README](../../README.md). Live runs consume quota.
Configure an outside-tree MCP mapping using environment references (for Parallel, `header_from_env`, as in the
[GAP runbook](../../RUNBOOK-gap.md)); never embed credentials. Use an outside-tree state root.

Catalog identity is SHA-256 of `catalogs/gap/tasks.yaml` file bytes:

- Battery and shipped catalog: `29f8fd1731cd0155f2a993688d8f1dfac7ad6ef1f6effc549185af026ad24443`

```sh
shasum -a 256 catalogs/gap/tasks.yaml
python3 scripts/check_reports.py
```

Calibrate the ten briefs immediately before the battery, then run every admitted task on all nine arms:

```sh
export SEW_MODE=standalone SEW_HARNESS_LIVE=1
sew gap calibrate --harness claude-code --model claude-opus-5-5 --reps 5 \
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
sew gap run --harness claude-code --model claude-opus-5-5 --reps 3 \
  --harness-auth account --state-root "$HOME/searchlight-state" \
  --run-root "$HOME/searchlight-gap-claude" \
  --provider-mcp-config "$HOME/searchlight-mcp.yaml" --prewarm-providers \
  --arm floor --arm ceiling --arm native --arm brave --arm tavily \
  --arm exa --arm parallel-web --arm firecrawl --arm perplexity
sew gap report "$HOME/searchlight-gap-claude"
```

This battery was graded with `SEW_GAP_JUDGES=claude-code` while codex quota was out, and the codex judge was added
afterwards from the stored payloads, run from the checkout that graded it:

```sh
sew gap agree "$HOME/searchlight-gap-claude" --catalog-root . --harness-auth account
```

A default run grades with both judges at once and needs no second step.

A fresh run measures the same method on today's models, indexes and sources; it is not an exact replay of these
numbers. Raw run bundles are not distributed. Report rendering is exact and offline:
`python3 scripts/check_reports.py --write`. See [methodology](methodology.md).

Validate the published reports and their site rendering without live harness calls:

```sh
python3 -m pytest tests/test_published_reports.py tests/test_build_site.py
```
