# GAP brief grading contract

`sew.gap.brief_grade.brief_schema(decision=True)` gives catalog authors the
JSON deliverable schema: `brief` markdown, `claims` with `text` and
`citation_urls`, and a decision `recommendation`. Research briefs omit the
required recommendation. Citation URL strings must contain a non-whitespace
character; empty and whitespace-only strings fail both schema and runtime
validation. Empty citation lists remain valid and force an unsupported label.
Empty claim lists receive an unsupported rate of 1,
so omission cannot earn a faithfulness pass.

The task's `rubric` is a catalog-relative YAML or JSON file. The verifier loads
it with `load_brief_rubric(task, catalog_dir)`. Its shape is:

```json
{
  "acceptable_recommendations": ["choose-new-plan"],
  "key_facts": [
    {
      "text": "The new plan costs seven units.",
      "weight": 2,
      "source_snapshot": {
        "url": "https://example.org/pricing",
        "retrieved_at": "2026-09-30",
        "text": "New plan: seven units."
      }
    }
  ],
  "pass_rule": {
    "recommendation_correct": true,
    "min_recall": 0.7,
    "max_unsupported_claim_rate": 0.25
  }
}
```

Authors must capture key-fact snapshots from primary sources. The rubric's
acceptable recommendation set is required for decision briefs. Matching is
exact and deterministic. `pass_rule` is optional; the example shows defaults.
Research briefs have `decision_correct: null` and no recommendation gate.

Call `grade_brief(task, rubric, answer, judges=judges,
capture_source=capture_source, arm=identity)` outside the arm workspace.
Judges must be ordered `Judge("claude-code", transport)` then
`Judge("codex", transport)`. Live callers can use the existing
`HarnessJudgeTransport` for each harness; tests use fixture callables. No live
judge or provider runs are needed to test this module.

The caller provides a bounded tool-side source capturer accepting a URL and
returning extracted source text. The grader invokes it at grade time, once per
unique URL, caching successes and failures for that grade. The caller owns
fetch policy and limits; this module does not fetch from the agent workspace
or use search excerpts. Exceptions, empty text and absent citations force an
unsupported label. Any failed cited URL makes that claim unsupported, even if
another citation is available. A fresh regrade recaptures sources.

Calibration's capturer accepts only credential-free HTTPS URLs resolving solely
to public addresses. It pins the resolved address, preserves hostname TLS
verification, disables proxies, and caps response bodies at 2,000,000 bytes.
It follows up to five redirects by hand. Every hop must pass the same HTTPS,
credential and public-address checks, so a citation of a moved or versioned
page is captured from where it now lives. An HTML response is reduced to its
visible prose, one block per line; scripts, styles and navigation are dropped.
Only footers explicitly marked `role="contentinfo"` outside `article`/`main`
are dropped as site chrome, including their nested footers. Ambiguous footers
and all footers inside `article`/`main` remain evidence, preserving substantive
qualifications. A documentation page shrinks from about 250,000 characters of markup
to about 9,000 of text. Each capture runs in a disposable subprocess with a
30-second wall-clock deadline covering DNS, connection setup, headers, body
reads and every redirect. On timeout the subprocess is killed and reaped;
partial text is discarded and the grader retains the capture failure as
unsupported evidence.

Persist the returned JSON record under the bundle's evaluations directory.
`source_snapshots` retains original URLs, capture timestamps, exact extracted
text and capture failures. Only the blinded evidence projection reaches the
judges; arm metadata and recommendation scoring remain outside that payload.
Each binary `fact_i` checks whether the brief expresses a fact established by
its rubric snapshot. Each binary `claim_i` checks entailment from the captured
sources that claim cites. URLs do not earn points.

The evidence sends each captured source once, as `sources[{id, text}]`. Each
claim lists the IDs of its sources in `source_ids`, so a page cited by ten
claims is not sent ten times. Judges receive complete readable sources, at most
100,000 characters each and 300,000 together. Sources are never sliced or cut
down to matching lines, because that could drop a governing heading, a
qualification or a negation.

A source over a bound is withheld, never cut, and every claim citing it is
forced unsupported. This is the same treatment as a source that could not be
captured: evidence the judges cannot read cannot support a claim.
- A single source longer than 100,000 characters is withheld as
  `source_char_cap_exceeded`.
- Sources are admitted in citation order until the next one would take the
  total past 300,000. That source is withheld as
  `sources_char_budget_exceeded`.

The rest of the cell is still graded. Calibration refuses an ungraded cell and
the battery runner stops on one, so a single long citation must not leave a
cell ungraded.

Captured `source_snapshots` records carry three fields beside the full text:
- `judged_chars`: source characters sent to each judge, zero for a withheld
  source;
- `excerpted`: always false here; older records may contain true for a
  selected excerpt;
- `withheld`: present only on withheld sources, and names the bound.

These fields are absent on capture failures and in older records that predate
evidence budgeting. Invalid-answer records omit `source_snapshots`.

Raw markup and repeated pages had pushed the claude-code judge past its
300,000-token budget. Reducing HTML to text and sending each source once keep
ordinary cells small. The bounds protect judge quota: 300,000 characters of
prose is well inside the 300,000-token judge budget.

Some servers compress a response no matter what the request asks for:
www.python.org sends gzip even to `Accept-Encoding: identity`. Capture decodes
gzip and deflate, and applies the 2,000,000-byte limit after decoding as well.
Any other encoding fails the capture.
Decoding requires one complete compressed stream: truncated streams,
concatenated gzip members and trailing data fail capture. This strict policy
can withhold legitimate multi-member gzip responses; their citing claims are
forced unsupported rather than judged using only the first member.
Capture failures retain the exception class in `source_snapshots[].error`.
For `ValueError`, `error_detail` retains the final 2,000 characters of the
exception message, including the decoder's encoding and rejection reason
(and any capture-worker traceback that fits). These diagnostics remain outside
the blinded judge payload. Older records and other exception types omit this field.

The primary judge determines weighted recall and unsupported rate. Both judges'
post-capture labels and scores are retained in `judge_scores`. `agreement`
reports binary Cohen's kappa (quadratic weighting on a two-category scale is
identical to unweighted kappa), the number of label pairs, disagreement IDs and
pass/fail dispute. Kappa is null when both judges use the same constant label;
it is undefined, not perfect agreement. `judge_record` retains raw responses
and the blinded payload digest; its generic dimension verdict is diagnostic,
while the GAP task pass rule determines the outcome. Judge failures leave the
outcome `not_applicable`, with agreement explicitly unmeasured. The agreement
reason comes from the shared judge record: `judge_unscored` when either judge
fails or returns invalid scores, even if the primary judge scored successfully.
Invalid brief JSON fails without invoking judges or source capture.
