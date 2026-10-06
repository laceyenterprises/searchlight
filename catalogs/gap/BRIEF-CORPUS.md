# GAP-09 brief seeds

The corpus contains six decision briefs and four research briefs, each based on
an independently dated primary-source event. The assessment date in each prompt
fixes what information the job could use; later source updates must not silently
change its answer. The decision prompts give business constraints and the two
recommendation strings needed by the deterministic grader, without telling the
agent a change occurred. Each research prompt asks for an operational synthesis,
not just a lookup.

The operator decision on 2026-10-01 fixes the authoring boundary at **2026-01-01**.
Every necessary event or dependency fix is published after that date. This is
not a claim about any model's training cutoff. GAP-13 must calibrate per harness
and model before admitting these tasks to a battery.

| Task suffix | Family | Publication | Event |
|---|---|---|---|
| org-migration | decision | 2026-01-12 | Personal-account conversion retired |
| models-production | decision | 2026-07-01 | Inference service retirement timeline |
| classroom-term | decision | 2026-08-27 | Classroom decommissioning and retained data boundaries |
| spark-editing | decision | 2026-08-04 | Workbench export and inference continuity |
| cli-trust | decision | 2026-09-03 | Linux repository signing-key transition |
| tarfile-upload | decision | 2026-08-12 | Extraction-filter traversal fixes in Python 3.10.21 |
| python-metadata | research | 2026-06-23 | Release-metadata API authentication bypass |
| python-security-march | research | 2026-03-03 | Python maintenance-batch security fixes |
| npm-token-scope | research | 2026-07-31 | Token management restrictions and future publishing timeline |
| billing-reporting | research | 2026-08-04 | Billing Preview replacement and reporting coverage |

Full IDs are `gap-brief-<suffix>-2026`. npm is a brief about credential policy;
this does not introduce an npm code-task lane or package dependencies.

`tasks.yaml` contains the prompt, assessment budgets and hash-checked ceiling
excerpt. `hidden/<task-id>/` contains verifier-only assets:

- `rubric.json`: three key facts, weighted 5/3/2, with primary-source passage
  snapshots, publication/retrieval dates and SHA-256 hashes; the most important
  fact has the largest weight. Decision rubrics declare the acceptable choice.
- `oracle-excerpts.json`: the frozen primary-source excerpt set. Selected verbatim
  passages are extracted as readable text (without HTML/link markup); their
  concatenation is the same evidence as the catalog ceiling excerpt. These are
  selected passages rather than whole-page captures.
- `reference.json`: a source-grounded JSON brief, claim list and decision where
  required, with practical implications of the facts.
- `stale.json`: a well-formed brief applying the prior operating assumption. It
  omits the new facts and recommends the now-invalid route for decision tasks.
  Citations point at the same source as the reference: URL presence cannot
  establish faithfulness.

The pass rule requires a correct recommendation, recall of at least 0.7, and an
unsupported-claim rate of at most 0.25. The operator raised that last limit from
0.1 on 2026-10-03. In the first live calibration, the answer-excerpt arm wrote
cautious claims such as "the notice does not specify X". Each was true of the
roughly 400-character excerpt the agent saw, but contradicted by the full cited
page the judges read. At 0.1, one such claim among four to six failed a brief
that recalled every key fact. That rejected research briefs whose
answer-excerpt runs had perfect recall. The unsupported-claim rate is still
reported for every brief. Brief verifier command fields are lane
labels for schema and brief grading, not shell commands for the code verifier.
The catalog loader only stat-checks the hidden directory and rubric path; listing
never loads the answers or rubric contents.

`sew.gap.brief_validate.validate_brief_task` runs the reference/stale pair through
`grade_brief` with injected judges and a source capturer. It rejects ungraded
answers, disputed verdicts, reference failures and stale passes. CI uses reviewed
passage/paraphrase fixture judgments, with mutation tests proving failures are
content-sensitive. This checks the authored corpus and grader plumbing offline;
it does not demonstrate real model floor/ceiling separation or live judge
agreement. Real-model admission remains GAP-13's calibration step.

Run the offline corpus checks from the SEW module:

```sh
python3 -m pytest -q tests/test_gap_brief_corpus.py tests/test_gap_brief_grade.py tests/test_gap_catalog.py
```

Before changing a source passage, review its URL and publication date, rebind all
excerpt/snapshot hashes, and repeat both validity legs. Refreshing the corpus is
a reviewed catalog change; it does not mutate old run-bundle evidence.

## Budgets

Brief budgets stop runaway cells; they are not a spending target. Tokens per
cell and per success are reported beside the outcome, so a costly correct brief
should count as a success with its cost shown, not as a failure. The runner counts
`total_billable` tokens, including cache reads, and checks the token and
search-call limits after the cell ends. A cell over either limit is recorded as
`budget_exhausted` and fails.

The limits are set above the per-cell spend measured on the 2026-09-29 WSB
batteries:

| Spend measured | Claude Code | Codex |
|---|---|---|
| Median tokens, search arms | 20k–47k | 138k–298k |
| Maximum tokens | 320k | 574k |
| Maximum search calls | 32 | 33 |
| Maximum wall time | 182 s on search arms; 1,052 s with no search | 164 s |

Every brief gets 1,000,000 tokens, 60 search calls and 1,200 seconds. The
original 30,000-token and 12-call caps were below Codex's median spend on every
search arm. Under them, most search cells would have failed on cost alone.
