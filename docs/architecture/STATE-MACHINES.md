# Architecture state index

Generated from `state-machines.json` using the Agent OS shared SMV renderer.
Searchlight owns this optional-overlay index; it is excluded from the superproject index.

| Object | States | Edges | Missing enforcement states |
|---|---:|---:|---:|
| `searchlight.suite` | 4 | 8 | 3 |
| `searchlight.retry` | 3 | 7 | 2 |
| `searchlight.deferral` | 3 | 6 | 2 |
| `searchlight.availability` | 3 | 10 | 3 |
| `searchlight.calibration` | 4 | 7 | 2 |
| `searchlight.gap-run` | 3 | 6 | 2 |
| `searchlight.cell` | 10 | 20 | 0 |
| `searchlight.provider-call` | 6 | 12 | 0 |
| `searchlight.evaluation` | 3 | 6 | 0 |
| `searchlight.run-classification` | 10 | 56 | 10 |
