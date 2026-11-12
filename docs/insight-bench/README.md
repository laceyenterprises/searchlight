# INS: the insight bench

**Which search or research API finds the most verified insight that no other API finds?**

INS poses one versioned question set to every arm (one service, with its request frozen in a profile) on the
same day. It turns what each arm returns into atomic claims, pools and clusters the
claims per question, and checks every claim on the page that claim cites. It then values the
verified clusters blind. An arm is credited only for claims its own source supports. The headline
is **unique verified insight**: what you lose if you drop that arm.

INS generalizes the one-off [search API head-to-head](../../reports/2026-09-26-search-api-head-to-head/REPORT.md)
(2026-09) into a repeatable command. It keeps that study's method: pooled results, blind and shuffled
grading with a recorded seed, claims verified on their page, strict per-claim credit, values of 3/2/1/0,
unique-to-arm counts, cost per new claim, sign tests and bootstraps. Each vendor's request follows
its own current documentation: see [provider best practices](provider-best-practices.md). To run or
sponsor a new study, start with the [study kit](STUDY-KIT.md).

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="../readme/diagram-insight-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="../readme/diagram-insight-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="../readme/diagram-insight-dark.svg">
  <img src="../readme/diagram-insight-light.svg" alt="How an insight study works: a question set and frozen request profiles feed a shared query-writer, run once per question and stored; sew insight run sends every question to every service the same day, with a spend ledger, and stores each cell's request, raw response, cost and latency. Grading, with one fixed extractor and a blind judge: extract atomic claims with their source URLs, cluster the same finding per question, verify each claim on the page it cites, and value the verified clusters 0 to 3 blind in a seeded shuffle. sew insight report gives each service's unique verified insight.">
</picture>

The newest published study ranks each service by useful findings per question, with its 95% range; where ranges
overlap, the test cannot separate them. Every study is in the [reports index](../../reports/README.md).

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="../readme/study-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="../readme/study-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="../readme/study-dark.svg">
  <img src="../readme/study-light.svg" alt="Useful findings per question (Leading software factories and agent-platform architectures · 13 services · 24 questions · 2026-11-11): exa.research 26.9 (95% range 10.3–43.7, 9 questions); exa.search 26.2 (95% range 18.0–34.5, 24 questions); perplexity.research 24.7 (95% range 15.1–32.6, 9 questions); firecrawl.research 23.8 (95% range 9.0–38.1, 9 questions); parallel.search 22.9 (95% range 16.3–29.0, 24 questions). No clear winner: exa.research found the most useful findings per question (26.9), but its range overlaps 10 of the other 12 services&#x27;.">
</picture>

## Run it

```sh
# 1. See the plan: cells, estimated spend, arms that cannot run here, and profile
#    fields still to confirm. This makes no calls and writes nothing.
sew insight run --set software-factories-2026-11 --extractor-model MODEL_ID --dry-run

# 2. Run every default arm on every question. The run refuses to start if the estimate exceeds --max-usd.
SEW_HARNESS_LIVE=1 sew insight run --set software-factories-2026-11 --extractor-model MODEL_ID --max-usd 110

# 3. Extract, cluster, verify and value, with the extractor pinned by the run.
SEW_HARNESS_LIVE=1 sew insight grade --run software-factories-2026-11-20261111 --judge-model MODEL_ID

# 4. Score into <run>/report/. This is offline and free, and can be re-run any time.
sew insight report --run software-factories-2026-11-20261111
open ~/.local/share/sew/insight/runs/software-factories-2026-11-20261111/report/dashboard.html

# 5. Package a shareable, sanitized study.
sew insight publish --run software-factories-2026-11-20261111 --to reports/2026-11-12-software-factories/
```

- **Choosing arms and questions.**
  - `--arms` takes vendors (`exa`: every default profile for Exa) or arm ids (`exa.search`,
    `parallel.research.pro`).
  - `--questions Q01,Q24` limits the questions.
  - `--arm-max-usd` caps each arm; the default cap is twice the arm's estimate.
  - Start with one question across every arm (`--questions Q01`). It confirms the `unverified`
    fields for about $5.
- **The extractor model** (`--extractor-model`) is fixed for the whole run. It is recorded in the
  manifest, writes the shared queries, fills Parallel's `client_model`, and extracts claims.
  `grade` refuses a different extractor.
- **Keys** reach the run only through `CredentialResolver`.
  - A resolved key fills the profile's header slot (for example `<EXA_API_KEY>`), is scrubbed from
    every file written, and is never printed.
  - **Standalone:** `op run --env-file keys.refs.env -- sew insight run ...`. The env file holds
    `op://` references for `SEW_EXA_API_KEY`, `SEW_PARALLEL_WEB_API_KEY`, `SEW_FIRECRAWL_API_KEY`,
    `SEW_BRAVE_API_KEY`, `SEW_BRAVE_ANSWERS_API_KEY`, `SEW_TAVILY_API_KEY` and
    `SEW_PERPLEXITY_API_KEY`.
  - **Agent OS:** set the matching `*_API_KEY_REF` variables, and the host resolves them through
    the broker.
  - `brave.research` uses the plan-scoped Answers key (`brave-answers`).
- **Model calls** (the query-writer, extraction and judging) use the same harness auth as every
  other Searchlight judge (`--harness-auth broker` or `account`). No model API keys are used.

## Import an external arm

An arm produced outside the bench can join a run as a graded arm. The first use is a native
harness research run:

```sh
sew insight import --run software-factories-2026-11-20261111 --arm claude-native.agent \
  --claims claims.jsonl --label "Claude Code native WebSearch/WebFetch" \
  --note "freeform research brief mapped to questions afterwards; not posed each question verbatim"
```

- **Input.** `--claims` is JSONL with one claim per line: `{qid, claim, source_url, retrieved_via}`.
- **Cells.** Each question with claims becomes one `ok` answer cell. Its response is the claims as
  text with their URLs, so the run's own extractor re-extracts them, and verification, clustering and
  valuation treat them like any other arm. The pre-split claims are never trusted as they are.
- **Manifest.** The entry is marked `imported`, with the label, the note, the source file's sha256 and
  the cost basis: `reported` with `--usd`, otherwise `unknown`.
- **Refusals.** A question that is not in the set or the run is refused, and so is an arm id that
  collides with a profile. Only the imported arm's own cells are written; re-importing replaces
  exactly those.
- **Report.** Imported arms are marked `(imported)`, and the report prints their caveat. They did not
  run under the run's profiles, date or spend ledger, so their latency is n/a, and their cost is n/a
  unless `--usd` reported it.

## Fairness rules

The full list, with the vendor evidence behind it, is in
[provider best practices section 9](provider-best-practices.md#9-fairness-rules). The bench
enforces these rules:

1. **Same day, frozen parameters.**
   - The run id defaults to `<set>-<UTC date>`, so re-running the same command on the same day
     resumes that run.
   - The manifest freezes the hashes of the set, of `_shared.json` and of every profile, along with
     the extractor model. A resume refuses any change; start a new run instead.
   - Arms run side by side, each walking the questions in set order. The report lists any cell that
     ran on a different day from the run date.
2. **Each vendor at its best.**
   - A profile is the exact request body that vendor operation receives, and the exact headers,
     beta headers included. The body is chosen from the vendor's documentation as read on the frozen
     date, with docs links and a rationale per field.
   - Search arms and research arms are separate arms: `exa.search` and `exa.research` each earn their
     own credit.
3. **The same question, the same shared transforms.**
   - Questions go verbatim (`{{QUERY}}`) to every arm that takes natural language.
   - Research arms get the one shared `{{RESEARCH_BRIEF}}`.
   - The only rewrite is the shared query-writer. It runs once per question, before any arm, with a
     frozen prompt and the pinned model, and its output is stored in `query-writer/<Q>.json`.
     `{{OBJECTIVE}}` and `{{KEYWORD_QUERIES}}` reach only the arms whose docs prescribe them
     (Parallel, and Perplexity's multi-query).
   - Freshness questions apply each profile's `freshness_overlay`, dated from the run date.
4. **Same top-k, same evidence budget.**
   - At most 10 unique result URLs per search arm, in returned order.
   - Each result's returned text is cut to 6,000 characters, a post-step page to 20,000 and a research
     answer to 40,000.
   - `chars_delivered` records what the vendor actually sent.
5. **Strict credit.**
   - An arm is credited with a claim cluster only when one of its own claims in that cluster is
     supported by the page that claim cites.
   - A claim with no source counts as unsupported.
   - A claim whose page cannot be read earns no credit, and is reported separately.
6. **Blind valuation.**
   - Judging payloads carry no arm labels, and `assert_blind` refuses one that does.
   - The value judge sees cluster texts in a shuffle seeded by the recorded grading seed, with no URLs.
   - Tested vendors' brand names are redacted, unless the question or reader context names them.
   - Exa `notices` reach the extractor. Vendor `warnings` never reach a model.
   - Clusters are valued against the set's reader context, and against its baseline knowledge file if
     there is one. A finding the baseline already states scores 0.
7. **Frozen budgets, scored as returned.**
   - A research run that reaches its frozen `timeout_s` is stopped through the vendor's stop endpoint
     where one exists (Exa `/stop`), and scored on what it gathered. It is never re-run with a larger
     budget.
   - Transient failures (rate limits, timeouts without a stop endpoint, caps, missing keys) are
     retried by the next run of the same command. Within a cell, HTTP retries use bounded backoff
     (up to three retries), except paid async task POSTs, which make one attempt. An ambiguous
     timeout or 5xx retains one estimated charge; reconcile vendor state before resuming. Each
     new attempt must fit the remaining spend cap.
8. **Hard spend caps.**
   - A ledger in the run directory reserves each cell's estimate before the call, and records the
     measured cost after it. Measured cost means `costDollars`, `usage.cost`, `usage.credits` or
     `creditsUsed`; without it the list estimate is used.
   - A cell over the arm cap or the run cap is recorded as `skipped_cap`.
   - A run whose estimate exceeds the run cap is refused, so the arms that start first cannot use up
     the budget of the others.
9. **Pre-registered variants only.** Variant profiles such as `exa.research.xhigh` are opt-in. They
   are named on the command line and reported beside their primary arm.

## Scores

| Metric | Definition |
|---|---|
| Insight | a credited cluster valued 1 or more |
| Weighted | the sum of values (3 decision-changing fact or measured result, 2 specific new design detail, 1 corroborating or minor, 0 generic or trivia) |
| Unique | an insight credited to exactly one arm |
| Share of pool | the arm's weighted score over the pooled weighted value; shares overlap |
| Cost per (unique) insight | the arm's recorded spend over its (unique) insight count |
| Unsupported rate | (unsupported + uncited) / checked claims |
| Overlap matrix | the insights each pair of arms both earned |
| If you could only buy k | a greedy order by added weighted insight, with ties going to the cheaper arm |
| Per-question winners | the arm(s) with the top weighted score on each question |
| Is the lead real? | the leader against each arm on per-question weighted score: an exact sign test with Holm adjustment, and a seeded question-bootstrap 95% CI, each labeled with n |
| Profile fields to confirm | every `unverified` profile field, to check against the recorded requests and responses |

Repeat runs are comparable when they share the set hash, the profile hashes and the grading models.
`sew insight report` writes a **comparable key**, and appends the run to
`<state>/insight/history/<set>.json`. The report then tabulates every earlier comparable run.

## The result bundle

`sew insight report` writes `<run>/report/`:

| File | What it is |
|---|---|
| `summary.json` | Everything, versioned and checked against [`config/insight/summary.schema.json`](../../config/insight/summary.schema.json): the set, the sponsor and disclosure, each arm's profile hash, docs and unverified fields, per-arm metrics with n and 95% intervals, overlap, best-k, per-question winners, every cluster with its claims, arms, verdicts, values and sources, spend, and the caveats |
| `REPORT.md` | The human report, cross-linked like the summary |
| `dashboard.html` | One self-contained page: data embedded, no network, works from `file://` and when published |

**Every metric and every claim links to the raw files it came from:** the cell, the extract, the
cluster, verify and value files, the query-writer output and the ledger. The bundle is stable:
re-reporting an unchanged run writes identical bytes.

**The dashboard** has a view for each of these:
- the leaderboard, sortable by every metric;
- what only one arm found;
- the overlap heatmap, with counts in every cell;
- best-k marginal contribution;
- cost against unique insight, with marker shapes for search, research and imported arms;
- a per-question drill-down: clusters, arms, verdicts, values and sources;
- claim search;
- every raw file.

The arm filter applies to every view. The dashboard follows the system theme, or you can switch it.
It is keyboard navigable (arrow keys move between tabs) and readable at phone width. Every color
signal also has text or a shape.

**`sew insight publish`** packages a study under `reports/<yyyy-mm-dd>-<slug>/`, following the
2026-09 head-to-head:
- the bundle, the set, the frozen profiles, and generated `methodology.md` and `reproduce.md`;
- `data/`: the manifest, the ledger, the query-writer output, sanitized cells and every grading
  file;
- a link from `reports/README.md`.

Published data keeps claims, URLs, verdicts, values, metadata and excerpts of at most 300
characters. Captured page text and full vendor answer bodies stay local, and their links read
*local only*. The published bundle passes `scripts/check_reports.py`. The site leaderboard is a
curated list (`REPORTS` in that script), so adding a study there is a separate, reviewed step.

## Layout

```
<state>/insight/runs/<run-id>/
  manifest.json          set, _shared and profile hashes and bodies, run date, extractor model
  query-writer/<Q>.json  shared objective and keyword queries
  ledger.json            spend per arm, caps
  cells/<Q>/<arm>.json   request (headers with key slots only), raw response, items, notices,
                         cost (measured or estimate), latency, stopped_at_timeout
  grading/               grading.json (seed, model/auth requests and receipt descriptors), extract/, clusters/, pages/, verify/, judge/
  report/                summary.json, REPORT.md, dashboard.html
<state>/insight/history/<set>.json
```

Every artifact records a digest of its inputs, so a rerun redoes only what is missing, transient or
stale. `grading.json` also freezes the requested extractor/judge model and auth identities before
work begins. Resume refuses a different identity; use a fresh copy without `grading/` to regrade
with a different model or auth path. Legacy grades without a request record are reusable only
when their recorded descriptors match. A resume that makes no calls keeps its receipt metadata.
Raw vendor text stays in the state root and is never committed.

## Add a question set

Write `catalogs/insight/<set_id>.json`:

```json
{
  "set_id": "my-topic-2026-12",
  "title": "...",
  "frozen_on": "2026-12-01",
  "context_for_judges": "Who reads the findings and what counts as valuable to them.",
  "baseline": "my-topic-2026-12.baseline.md",
  "questions": [{"id": "Q01", "text": "...", "freshness_days": null}]
}
```

- `baseline` is optional. It names a file next to the set that holds what the reader already knows.
- Never edit a set after its first run. Copy it to a new `set_id` instead, so history stays comparable.

## Add or change an arm

Profiles are data. A new vendor product, Exa 3.0's for example, is one file
`config/insight/profiles/<arm>.<operation>[.<variant>].json`. Response parsing goes through that
vendor's adapter in `sew/providers.py`, with a generic fallback that reads any record carrying a
web URL.

| Field | Meaning |
|---|---|
| `arm`, `operation`, `variant` | the vendor, the product and an optional pre-registered variant; together they form the arm id and the file name |
| `endpoint` | `"POST https://..."` or `"GET https://..."`. Anything else (a CLI, an SDK) loads as **not runnable** |
| `headers` | the exact headers. A key slot `<EXA_API_KEY>` names the `CredentialResolver` id; a profile whose slot no id serves is not runnable |
| `params` | the exact body, using `{{QUERY}}`, `{{RESEARCH_BRIEF}}`, `{{OBJECTIVE}}`, `{{KEYWORD_QUERIES}}` (a list, or spread with `{{KEYWORD_QUERIES...}}`), `{{EXTRACTOR_MODEL}}`, `{{SESSION_ID}}` and the date slots |
| `freshness_overlay` | applied when a question has `freshness_days`. A dotted key (`advanced_settings.source_policy`) sets or merges; `<path>_suffix` (`query_suffix`, `messages[0].content_suffix`) appends text |
| `post_steps` | `[{op: fetch, endpoint, params with {{TOP_K_URLS}} (one batch call) or {{URL}} (one call per page), top_k}]` |
| `async` | `poll_endpoint` with an `{id}`-style slot, optional `result_endpoint`, `interval_s`, `timeout_s`, `terminal_states`, and `on_timeout` (a stop endpoint) |
| `top_k`, `pace_seconds`, `concurrency` | results kept, the courtesy gap after each call, and parallel runs per arm (default 4 for async arms, otherwise 1) |
| `est_cost_usd_per_question`, `cost_model` | the reservation and dry-run estimate. Measured spend always wins; reported credits are priced at `cost_model.usd_per_credit`, or at the bench's upper-bound rate |
| `unverified_fields`, `unverified` | fields the docs did not confirm, shown by `--dry-run` and in the report |
| `default_arm`, `optional` | `false` or `true` respectively keep the arm out of runs that do not name it; variants are never default |
| `docs`, `rationale` | the evidence for every choice |

## Cost

At the frozen estimates, the 24-question software-factories set costs about **$108** for the twelve
default arms. Per question:
- **Search arms:** $0.005 to $0.036.
- **Research arms:** $0.30 (Parallel ultra), $0.50 (Exa high, Brave research), $0.60 (Perplexity
  high), $1.00 (Tavily pro) and $1.50 (Firecrawl, 1,000-credit cap).

`--dry-run` prints the exact figure. Measured spend replaces each estimate as cells finish. Grading
runs on harness auth: about one model call per cell to extract, one per question per 50 candidate
pairs to cluster, one per cited page to verify, and one per question to value.

## Deviations and deferred work

- **Native and optional arms do not run yet.** A native run done elsewhere can join through
  `sew insight import`, with its caveat recorded.
  - Covered: `claude-native.search`, `codex-native.search` and `claude-native.research`, which needs
    an Anthropic API key that no `CredentialResolver` id serves, plus the optional `gemini-grounding`
    and `xai-grok`.
  - They load, appear in `--dry-run` as not runnable, and are documented in the best-practices
    page.
  - The harness layer has no one-question research spawn that returns a cited answer with metered
    cost. Adding one is a small `HarnessSpec` capability, plus a runner for harness profiles.
- **Query-writer temperature.** Harness transports cannot set temperature 0. The writer runs once
  per question and its output is stored, so every arm and every resume sees the same text.
- **Brave.**
  - LLM Context (`/res/v1/llm/context`) is called as profiled. Its `grounding.generic[].snippets`
    are read by the generic parser, not by a `BraveProvider` `llm_context` operation (deferred).
  - Brave research streams SSE. The bench reads the stream as one body, and the extractor reads
    the answer and cited URLs from it. Its cost is the estimate until `<usage>` is parsed.
- **Variants shipped.**
  - Available: `exa.research.xhigh` (EXA-R-XHIGH), `parallel.research.pro` (PAR-R-PRO),
    `tavily.research.mini` (TAV-R-MINI) and `exa.search.std` (EXA-S-STD, the fallback if dynamic
    highlights is refused).
  - Not yet written: EXA-S-OBJ, EXA-D, PAR-R-RESP, TAV-S-SPLIT3, FC-S-TOP3 and PPLX-S-SINGLE. The
    best-practices page names them; each needs one profile file.
- **Exa 3.0 adapter changes (section 1a).**
  - Made: item 1 (type-enum guidance), item 4 (`warnings` and `notices` recorded as counts; notices
    go to the extractor, warnings never do) and item 5 (`resolvedSearchType` is no longer recorded).
    The bench itself sends exact profile bodies and headers, including the `Exa-Beta` header and the
    Agent `/stop` endpoint.
  - Deferred, because the bench does not route through `ExaProvider._request_body`: items 2
    (`lite`'s strict options), 3 (a `dynamic` content view for the retrieval lane) and 9 (an Agent
    operation in the adapter).
  - Deferred, for other suites: items 7 (categories) and 8 (findSimilar). Items 6 and 10 need no
    change.
  - `price-table.yaml` does not yet have `search:lite`.
- **Reuse notes.**
  - `claim_verification.py` (DSB vendor-claim separability) and `entity_resolution.py` (DSB identifier
    scoring) solve different problems, so INS does not use them.
  - Page verification reuses GAP's bounded HTTPS source capture.
  - Model calls reuse `HarnessJudgeTransport`, which gained a `json` contract mode.
  - Cells reuse the vendor adapters' parsers, `CredentialResolver`, `measured_spend` and the shared
    backoff.

## Internals: corporate grading on Agent OS

For corporate Claude grading on Agent OS, pin the extractor/judge models and
pass `--harness-auth broker --claude-broker-provider claude-code-corp` to
`sew insight run` (the shared query-writer) and `sew insight grade`. The updated SDK host
forwards the model entitlement and provider; no `CLAUDE_MODEL_ID` or `CLAUDE_BROKER_PROVIDER` environment overrides
are needed. `grading/grading.json` records the auth source and broker provider
for executed extractor/judge calls. A provider selector requires broker auth and Claude Code
for both roles; broker grading requires both models to be pinned (the extractor may come from
the manifest). Explicit model/provider flags take precedence over selector environment variables.
A requested provider must be confirmed in the broker receipt or the call is refused.
Deploy the updated agent-os SDK before floating Searchlight; older hosts fail with a named
model/provider-selector error. Standalone account login is unchanged.
