# GAP replication methodology

This battery repeats the [2026-10-03 GAP methodology](../2026-10-03-search-gap-bench/methodology.md) on one
agent: calibration per task (five floor and five ceiling repetitions; admission requires the floor to pass at
most once and the ceiling at least four times), then every admitted task three times on nine arms.

Differences from the 2026-10-03 battery:

- **Catalog.** The current GAP catalog (SHA-256 `29f8fd1731cd0155f2a993688d8f1dfac7ad6ef1f6effc549185af026ad24443`), in which python-security-march's
  answer excerpt carries the date its top fact needs. 8 of the 10 briefs were admitted.
- **Agent.** Claude Code on claude-opus-5-5 only.
- **Built-in search.** The native arm pre-approves WebFetch as well as WebSearch, so the agent can open pages.
- **Provider tools.** Every provider arm made provider calls in every run; Parallel's credential is passed as a
  header file the workbench writes per run.
- **Judging.** The claude-code primary judge decides every verdict, as before. The codex judge, which measures
  agreement, was out of quota during the battery. It scored the same stored payloads on 2026-10-07 with
  `sew gap agree`, which rebuilds each payload from the stored source snapshots and calls codex only when the
  payload's SHA-256 matches the primary's and the stored labels reproduce the verdict.

Statistics are unchanged: Wilson 95% intervals on pass rates, a seeded paired task bootstrap (10,000 resamples)
for gap closure, exact McNemar tests against the floor and the built-in arm, and tokens per success counted over
all agent tokens in the arm, failures included. The comparison with the 2026-10-03 battery uses two-sided Fisher
exact tests on the seven shared briefs, because the two batteries' runs are not paired.

See [recorded results](REPORT.md), [summary](summary.json), [admission counts](calibration.json) and
[reproduction](reproduce.md).
