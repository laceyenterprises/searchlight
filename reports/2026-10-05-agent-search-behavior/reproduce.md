# Reproduce the agent search behavior analysis

The script needs only stored run bundles. It makes no network or model calls.

```sh
python3 scripts/analyze_search_behavior.py \
  /path/to/state/gap-battery/runs/claude-code-combined \
  /path/to/state/gap-battery/runs/codex-combined \
  --catalog catalogs/gap/tasks.yaml --json gap-behavior.json

python3 scripts/analyze_search_behavior.py \
  /path/to/bakeoff-full-suite-run \
  /path/to/bakeoff-supplemental-suite-run \
  /path/to/bakeoff-supplemental-smoke-run --json bakeoff-behavior.json
```

The bakeoff analysis includes the full run, the supplemental four-provider run and its
four-cell smoke run, matching the historical 436-cell selection. The corrected extraction
adds 23 GAP and 13 bakeoff user messages to the query totals. Source coverage is recomputed
from returned results only; historical source counts remain unchanged.

Each argument is a suite-run directory containing `run-index.json`, as written by
`sew run` and `sew gap run`. The published figures were produced from the private run
bundles of the two batteries, which are not distributed, so exact replay is not possible
here. Running the same script over a new battery of your own measures the same
behaviors on your runs. The script's unit tests (`tests/test_analyze_search_behavior.py`)
pin the classification rules on synthetic transcripts.

Catalog identity for the GAP primary-source check:

```sh
shasum -a 256 catalogs/gap/tasks.yaml
python3 scripts/check_reports.py
```

The historical GAP battery hash is recorded in [summary data](summary.json). Primary
sources for tasks added after the battery do not affect the published rows.
