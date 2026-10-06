# GAP report bundle contract

`hq-sew gap report <suite-run> [--calibration <record>] [--seed 0]`
writes reports/gap-report.json and reports/gap-report.md and prints the
Markdown. `--output-dir` changes the destination; `--price-table` overrides
SEW's price table. Reporting is offline and never invokes an arm or judge.

`hq-sew gap explain <suite-run> --task <id> --arm <arm>` prints repetitions,
escaped test IDs, visible test counts, searches and measured token mix.
For sparse token mixes, omitted output or reasoning buckets contribute zero
to combined output; missing input/cache buckets remain unknown (—), and an
absent or empty mix is shown as unknown.
`--format json` returns the complete cell evaluations, including verifier
stdout/stderr, reruns, flakes, and brief scores, source snapshots and agreement.
In text explanations, omitted or null optional evidence collections (hidden
and visible lanes, lane tests, reruns, flakes, and agreement) render as empty;
missing visible-test coverage and agreement scores remain unknown. JSON
explanations retain the original evaluation values, including nulls.

The GAP runner supplies SEW's `runner-state.json` and `run-index.json`. Each
index entry identifies `task_id`, positive `repetition`, `provider_id`,
`harness_id`, `model_profile`, and `run_dir` (relative to the suite or absolute).
There must be one final cell per task/repetition/arm, with a single calibrated
harness/model. A run bundle uses the existing SEW run, metrics, spawn metadata,
provider-call and transcript records. `spawn-metadata.json` must record the
calibrated `model_id`; absent identity is retained as an unscored mark, and a
conflicting identity is refused. Contamination audit overrides any pass.

Persist the out-of-band code verifier or brief grader result at
evaluations/gap-outcome.json. Calibration bundles can instead contain
evaluations/calibration-outcome.json; otherwise `run.json.evaluation_ref`
identifies the grade. Records with verifier infrastructure errors remain
ungraded. Timeouts and budget exhaustion count as task failures, with their
statuses preserved. No reporting step runs tests or captures cited sources.

Embed the calibration record under `runner-state.json.calibration`, reference a
JSON path there (relative to the suite), or supply `--calibration`. It contains
`harness`, `model`, and GAP-05's `tasks` entries: `task_id`, `kind`, `family`,
`verdict`, `reason`, date and floor/ceiling rates. Rejected and retired tasks
leave the scored population and remain in the appendix; excluded run cells
are recorded individually in JSON.

Headline outcomes, defects and usage cover gap tasks. Controls have separate
paired overhead and harm fields. Family tables include their own outcomes,
usage and paired tests; the control family reports its own outcomes and usage.
Markdown coverage uses labelled counts for paired tasks/cells, exclusions,
undefined bootstrap draws, controls and measured/unknown/estimated usage.
The JSON report retains complete exclusion records and measurement fields.
Arms are displayed alphabetically, with no ranking. Missing defect counts
(e.g. briefs) are unknown, not zero; measurement counts accompany aggregates.

Closure is the ratio of equally weighted task-mean arm-minus-floor and
ceiling-minus-floor deltas. Match all three arms on `(task, repetition)` before
averaging within a task. Resample whole tasks 10,000 times with Python's
`random.Random(seed)`, keeping arms and repetitions together; report a 95%
percentile interval. Nonpositive reference gaps and undefined bootstrap draws
withhold the interval and are counted. Closure is not clipped to [0, 1].
Exact two-sided McNemar comparisons against native and floor use matched
`(task, repetition)` gap cells; missing or unscored pairs are listed. P-values
are unadjusted for multiple comparisons. The exact integer binomial tail remains
safe beyond 1,024 discordant pairs; no normal approximation is substituted. Wilson intervals describe pass rates;
Ungraded cells withhold the overall pass rate but retain a labelled graded rate.

Token statistics reuse SEWTOK-01: measured usage only; input/cache/output mix
includes reasoning in output; tokens/success uses measured graded cells and
the successes among those same cells. Unknown and estimated usage are counted.
Dollars/success divides known spend across gap cells by their scored successes;
partial pricing is explicitly a lower bound, including known model spend when
search pricing is absent. With no known spend or no successes it is undefined.

Denied shell-network attempts are a behavior metric, separate from contamination
and pass rates. JSON records `denied_network_attempts` for each included or
excluded cell; Markdown shows the total recorded count. Legacy bundles without
the field contribute zero recorded attempts. Calibration reference cells retain
the metric along with excluded contaminated attempt directories.

JSON also records `config_neutralized_network_attempts` per included or excluded
cell; Markdown and calibration summaries show the recorded total. This counts
only detected shell-network calls exempted by the GAPPIP enforced offline pip
configuration and paired-result contract in WORKSPACE.md. Ordinary offline
requirements installs contribute zero, even on success or missing distributions.
The count is separate from denials, contamination and pass rates. Calibration
stores the accepted cell's count, excluding earlier attempts from its summary;
legacy records without this field contribute zero.

Calibration records and rendered summaries include `excluded_contaminated_attempts`,
the total excluded reference attempts across both arms. Admission rates remain
conditional on clean accepted attempts; operators should inspect this headline
and retained attempt directories when interpreting resampled reference behavior.
Malformed or absent bundle audit counts contribute zero to GAP reports.
