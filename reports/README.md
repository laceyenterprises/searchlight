# Reports

- [Web search bakeoff](2026-09-29-web-search-bakeoff/REPORT.md): the retrieval battery and its later regrade. Read the regrade before quoting the rankings.
- [Search gap bench](2026-10-03-search-gap-bench/REPORT.md): calibrated job outcomes on Claude Code and Codex, including the Codex provider reruns and the Claude Code availability caveat.
- [Search gap bench replication](2026-10-06-search-gap-replication/REPORT.md): the Claude Code battery run again on the current catalog with the fixed apparatus (built-in search can open pages). Search still decides the outcome; the provider ranking did not repeat. Primary-judge verdicts; codex agreement follows.
- [How agents used search](2026-10-05-agent-search-behavior/REPORT.md): query style, fetching from memory and primary-source retrieval, computed from the stored transcripts of the two batteries by `scripts/analyze_search_behavior.py`.
- [Search API head-to-head](2026-09-26-search-api-head-to-head/REPORT.md): Exa, Tavily, Parallel and Firecrawl called directly, with no agent, on a pre-registered research workload, graded blind and verified on the page. Unlike the other reports, its directory also publishes the experiment's own record (`record/`), code (`code/`) and sanitized data (`data/`), and, for its Monitors probe only, the raw provider responses (`data/stage-b/runs/SM/raw/`). Read its context note before quoting it.

The [leaderboard, infographic and rendered reports](../README.md#how-the-leaderboard-stays-current) are generated from these
directories by `scripts/build_site.py` and published to GitHub Pages.

Each directory contains REPORT.md, summary.json, calibration.json, methodology.md
and reproduce.md. The summary stores labelled table cells at their recorded display
precision and the narrative that explains them. It is a transcription of the
findings, not fabricated per-cell observations or a newly calculated report.
REPORT.md is byte-identical to the transcription rendered from summary.json.
The rendering check detects transcription drift; it does not independently
recalculate numeric claims from observations.
Calibration records contain published admission counts only; WSB marks calibration
as not applicable.

Methodology explains denominators, intervals, cost coverage and changes to grading.
Reproduction instructions use standalone account auth and environment credentials,
with exact historical and shipped catalog hashes. A new live run evaluates the
method; changing sources, models, grading and catalogs can change its results.
Private evidence is unavailable here, so exact replay of the historical aggregates
is not possible. Raw run bundles, transcripts and provider responses are omitted; the one exception is the
search API head-to-head's Monitors probe, whose raw responses are published.

From the repository root, validate all reports offline:

```sh
python3 scripts/check_reports.py
SEW_MODE=standalone python3 -m pytest -q tests/test_published_reports.py
```

To edit a transcription, review summary.json first, then run
`python3 scripts/check_reports.py --write`. The suite rejects prose or table drift,
generic private identifiers, catalog hash drift, calibration admission-rule or
transcription inconsistencies, unresolved template expressions and broken links. Calibration catalog identity must
match the recorded historical battery hash. The scrub scans Markdown, JSON and
YAML publication text, including private paths in file URIs, and skips binary assets.
Invalid UTF-8 or malformed publication JSON produces gate errors while validation
continues for other reports and the text scrub.

Known private account or host names are an optional private check. Supply
`SEW_REPORT_PRIVATE_NAMES` as a newline-separated list of whole word/hyphen tokens
through the operator environment or private CI secret configuration. Matching is
case-insensitive; private-name scrub diagnostics report the file rather than the
matched values. Keep the list outside Git. When the variable is absent or empty, only the generic scrub runs;
public CI does not claim to check deployment-specific names. A private publication
lane must provision this input if that coverage is required.
See the [workbench reference](../WORKBENCH.md) for configuration and metering.
