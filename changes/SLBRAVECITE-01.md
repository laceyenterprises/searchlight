Insight reports now explain an arm's score when all its extracted claims are
uncited. The note is derived from claim counts and appears on report, dashboard
and studies ranking rows, including older summaries with no stored note.
Summary bundles may include optional dated `corrections` strings, rendered in
the report and dashboard before the results. No scoring or grading rules change.

Every stored arm note now appears on report, dashboard and studies ranking rows,
including `claude-native.agent`'s imported research caveat. The Markdown arm-name
cell includes the note after the label; consumers should use `summary.json`'s
`arm_id` for stable identifiers.

Re-scoring retains corrections from `<run>/report/summary.json`; notes must begin
with a `YYYY-MM-DD` date, a colon or spaced em dash, and non-empty text. Corrections
made only in a published bundle must be copied back to that run's summary before
re-scoring or publishing.
The corrections panel uses the dashboard's comparison-note styles and a named
section. Tests keep statistics independent and restore temporary import paths;
the interactive leaderboard tolerates a missing arm metadata row.
