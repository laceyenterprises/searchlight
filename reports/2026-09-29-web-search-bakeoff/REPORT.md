# WSB live battery — initial results (2026-09-29)

> **Correction (2026-10-06): the native arm's page fetches were mostly refused.** The arm exposed
> WebSearch and WebFetch but pre-approved only WebSearch, and headless Claude Code refuses tools that
> are not pre-approved. WebFetch was refused in 40 of 54 native cells (110 calls).
> Provider arms were unaffected. The native rows therefore understate Claude Code's own web tools.
> The configuration is fixed and pinned by a test; the recorded numbers below are unchanged.
> Details: [agent search behavior](../2026-10-05-agent-search-behavior/REPORT.md).

**Pack:** `web-search-bakeoff` (WSB) on the Search Evaluation Workbench (SEW).
**Harness / model:** claude-code, Claude Opus 5.5 (`claude-opus-5-5[1m]`), authenticated execution on the original host.
**Status:** historical initial grading; headline and Results tables are superseded by the
[2026-09-30 regrade](#update-2026-09-30-operator-decisions), where Firecrawl leads at 90%. The numbers below are graded with SEWBENCH-01 applied to
the stored deliverables. The same deliverables graded without those fixes are summarised in
[Failure dive](#failure-dive) and were materially wrong.

## Headline

Historical figures under the initial grader: rankings and cost lower bounds here apply only to that
grading, not the later operator-ruled catalog.

On the 14 `competitive` production tasks (42 cells per arm), every search arm except Brave scores
above answering without search:

- **Firecrawl and Perplexity lead at 83%.** Exa is at 81%, Tavily 79%, native web search and
  Parallel 76%, no-search 71% and Brave 69%.
- **The 95% intervals overlap.** At n=42 per arm the ordering is directional, not conclusive: no
  arm's difference from no-search or from native has an interval that excludes zero.
- **Search matters most on tasks that need live data.** On the cloud egress pricing table only
  Firecrawl and Perplexity went 3/3 (Brave 2/3, the rest 0/3). The AWS pricing pages need
  JavaScript rendering, and most arms reported the intra-AZ cell as unavailable, honestly.
- **Among fully priced arms, native search costs least per success** ($0.167), followed by
  Tavily ($0.206). Perplexity (at least $0.148; 37/42 cells priced) and Firecrawl (at least
  $0.159; 39/42 priced) are lower bounds, excluded from the cost ranking; Perplexity's `ask`
  spend is unmetered. Brave and no-search are also partially priced (at least $0.28 each);
  no-search had a few long runaway answers.
- **Brave is last,** failing `unanswerable-nonexistent-postgres-guc` and
  `upstream-diagnosis-node-openssl3-md4` in all three repetitions.

## What ran

| Run | Arms | Cells | Window (UTC) |
| --- | --- | --- | --- |
| first arm group | no-search, native, brave, tavily | 216 | 2026-09-29 03:29 – 13:26 |
| second arm group | exa, parallel-web, firecrawl, perplexity | 216 | 2026-09-29 13:39 – 22:49 |


- **Tasks:** all 18 production tasks ([production task catalog](../../catalogs/production/tasks.yaml)), × 3 repetitions.
  - 14 are `competitive`.
  - 4 are `expected_fail` by design: no retrieval surface can answer them, and the honest
    deliverable is a partial answer or a decline.
- **Arms:**
  - Provider arms expose one vendor MCP server each, and nothing else.
  - The native arm uses the harness's own web search.
  - The no-search arm has no network tools.
- **Pinned MCP servers:**
  - [@brave/brave-search-mcp-server](https://www.npmjs.com/package/@brave/brave-search-mcp-server) version 2.1.4
  - `tavily-mcp@0.2.22`
  - `exa-mcp-server@3.4.1`
  - `firecrawl-mcp@3.26.0`
  - [@perplexity-ai/mcp-server](https://www.npmjs.com/package/@perplexity-ai/mcp-server) version 1.3.0
  - Parallel's hosted Search MCP (`https://search.parallel.ai/mcp-oauth`, authenticated) via
    `mcp-remote@0.14.3`
- **Grading:**
  - Deterministic validators where a task has one.
  - Otherwise a blinded claude-code judge scoring the task's rubric.
  - `bakeoff grade --regrade` re-scores the stored deliverables. Nothing was re-run.
- **Cost:** model tokens are priced at the published Opus 5.5 rates. Vendor spend is metered per
  MCP call where a tariff exists. It is unknown for Exa, Parallel and Perplexity's `ask` tool,
  which report no spend over MCP.
  In the tables, `partial` is the priced cells' spend divided by all of the arm's successes.
  Unpriced cells can only add spend, so each `partial` figure is a lower bound on the full-arm
  $/success, and these arms are excluded from the cost ranking. `bakeoff report` divides by
  the successes among priced cells instead, which gives $0.172 for Perplexity, $0.163 for
  Firecrawl and $0.29 for Brave on the competitive set, and no figure for Exa or Parallel on
  the expected-fail set, where every success is in an unpriced cell.

## Results

All Results tables below use the historical initial grading. The `ungraded` column counts delivered answers that could not be graded. Cells that exhausted their budget before delivering are counted under `budget-exhausted` and as failures, rather than under `ungraded`.

### Competitive tasks (14 tasks × 3 repetitions)

| arm | pass | 95% CI | $/success | priced | tokens/cell | provider calls | budget-exhausted | ungraded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| firecrawl | 35/42 (83%) | 69–92% | partial $0.159 (39/42 priced) | 39/42 | 55k | 219 | 2 | 0 |
| perplexity | 35/42 (83%) | 69–92% | partial $0.148 (37/42 priced) | 37/42 | 42k | 145 | 0 | 0 |
| exa | 34/42 (81%) | 67–90% | unpriced | 0/42 | 54k | 105 | 0 | 0 |
| tavily | 33/42 (79%) | 64–88% | $0.206 | 42/42 | 52k | 119 | 0 | 0 |
| native | 32/42 (76%) | 61–87% | $0.167 | 42/42 | 28k | 256 | 0 | 0 |
| parallel-web | 32/42 (76%) | 61–87% | unpriced | 0/42 | 54k | 90 | 1 | 0 |
| no-search | 30/42 (71%) | 56–83% | partial $0.285 (41/42 priced) | 41/42 | 15k | 0 | 0 | 0 |
| brave | 29/42 (69%) | 54–81% | partial $0.280 (41/42 priced) | 41/42 | 65k | 154 | 0 | 0 |

### Competitive deltas against controls

Each arm against each control over the same 14 tasks × 3 repetitions. Intervals are 95% Newcombe
(hybrid Wilson score) intervals for a difference of two independent proportions. They ignore the
task pairing, so they are conservative. Token ratios compare mean tokens per cell. Provider arms
and their controls come from two runs about ten hours apart (see [Caveats](#caveats)).

| arm | vs no-search: pass Δ (95% CI) | vs no-search: tokens | vs native: pass Δ (95% CI) | vs native: tokens |
| --- | --- | --- | --- | --- |
| firecrawl | +12 pp (−6 to +29) | 3.49× | +7 pp (−10 to +24) | 1.95× |
| perplexity | +12 pp (−6 to +29) | 2.63× | +7 pp (−10 to +24) | 1.47× |
| exa | +10 pp (−9 to +27) | 3.39× | +5 pp (−13 to +22) | 1.90× |
| tavily | +7 pp (−11 to +25) | 3.29× | +2 pp (−15 to +20) | 1.84× |
| native | +5 pp (−14 to +23) | 1.79× | — | — |
| parallel-web | +5 pp (−14 to +23) | 3.44× | +0 pp (−18 to +18) | 1.92× |
| no-search | — | — | −5 pp (−23 to +14) | 0.56× |
| brave | −2 pp (−21 to +17) | 4.13× | −7 pp (−25 to +12) | 2.31× |

### Expected-fail tasks (4 tasks × 3 repetitions)

Passing here means an honest partial answer or decline, which the validator can confirm.

| arm | pass | 95% CI | $/success | priced | tokens/cell | provider calls | budget-exhausted | ungraded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| exa | 5/12 (42%) | 19–68% | partial $0.046 (6/12 priced) | 6/12 | 67k | 30 | 0 | 0 |
| no-search | 3/12 (25%) | 9–53% | partial $2.827 (9/12 priced) | 9/12 | 43k | 0 | 0 | 0 |
| native | 3/12 (25%) | 9–53% | $0.273 | 12/12 | 16k | 28 | 0 | 0 |
| brave | 3/12 (25%) | 9–53% | partial $0.763 (11/12 priced) | 11/12 | 66k | 45 | 1 | 0 |
| firecrawl | 3/12 (25%) | 9–53% | partial $0.672 (10/12 priced) | 10/12 | 75k | 104 | 2 | 0 |
| perplexity | 3/12 (25%) | 9–53% | partial $0.197 (9/12 priced) | 9/12 | 36k | 30 | 0 | 0 |
| parallel-web | 2/12 (17%) | 5–45% | partial $0.116 (6/12 priced) | 6/12 | 60k | 34 | 1 | 0 |
| tavily | 1/12 (8%) | 1–35% | partial $1.923 (11/12 priced) | 11/12 | 64k | 49 | 1 | 0 |

### Per task

| task | outcome | no-search | native | brave | tavily | exa | parallel-web | firecrawl | perplexity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `change-detection-kubernetes-dockershim-removal` | competitive | 2/3 | 2/3 | 1/3 | 3/3 | 2/3 | 3/3 | 3/3 | 3/3 |
| `change-detection-openapi-30-to-31` | competitive | 1/3 | 1/3 | 0/3 | 0/3 | 2/3 | 2/3 | 0/3 | 1/3 |
| `competitive-table-cloud-egress-pricing` | competitive | 0/3 | 0/3 | 2/3 | 0/3 | 0/3 | 0/3 | 3/3 | 3/3 |
| `competitive-table-copyleft-obligations` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| `list-build-kubernetes-122-api-removals` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 2/3 | 2/3 | 3/3 | 3/3 |
| `list-build-python313-pep594-removals` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| `list-build-sca-tool-shortlist` | competitive | 0/3 | 2/3 | 2/3 | 1/3 | 3/3 | 3/3 | 1/3 | 3/3 |
| `multi-hop-cve-to-fixed-release` | competitive | 3/3 | 1/3 | 3/3 | 3/3 | 3/3 | 2/3 | 2/3 | 1/3 |
| `multi-hop-python-feature-peps` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| `unanswerable-nonexistent-postgres-guc` | competitive | 2/3 | 3/3 | 0/3 | 3/3 | 2/3 | 2/3 | 3/3 | 1/3 |
| `unanswerable-private-company-audited-arr` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 1/3 | 3/3 | 3/3 |
| `unanswerable-python4-ga-date` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| `upstream-diagnosis-node-openssl3-md4` | competitive | 1/3 | 2/3 | 0/3 | 2/3 | 2/3 | 2/3 | 2/3 | 2/3 |
| `upstream-diagnosis-urllib3-libressl-import-error` | competitive | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| `change-detection-unversioned-runtime-drift` | expected_fail | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| `competitive-table-unpublished-enterprise-pricing` | expected_fail | 3/3 | 3/3 | 3/3 | 1/3 | 2/3 | 1/3 | 2/3 | 3/3 |
| `multi-hop-npm-semver-dependents` | expected_fail | 0/3 | 0/3 | 0/3 | 0/3 | 3/3 | 1/3 | 1/3 | 0/3 |
| `upstream-diagnosis-unindexed-ordering-regression` | expected_fail | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |

## Findings

1. **Search earns its cost on live or long-tail data, not on stable knowledge.**
   - Tasks whose answers are stable and well known were 3/3 in every arm, including no-search:
     copyleft obligations, PEP 594 removals, Python feature PEPs and the urllib3 diagnosis.
   - The separation comes from the tasks that need current data:
     - the egress pricing table;
     - the SCA tool shortlist (no-search 0/3; Exa, Parallel and Perplexity 3/3);
     - the dockershim change-detection task.
2. **Rendering matters for vendor pricing pages.** Firecrawl (which renders JavaScript) and
   Perplexity were the only arms to read AWS's intra-AZ transfer pricing. The others honestly
   reported it unavailable, and the validator fails an unavailable cell.
3. **Declining is not the default for search arms.** `unanswerable-nonexistent-postgres-guc`
   separates the arms: Brave 0/3 and Perplexity 1/3 against 3/3 for native, Tavily and Firecrawl.
4. **Exa handles impossible tasks best** (5/12 honest partials on the expected-fail set). It is the
   only arm to go 3/3 on `multi-hop-npm-semver-dependents`, where the honest move is to state the
   method and its limits rather than invent a ranking.
5. **Provider arms use more reported tokens per cell.** The MCP provider arms report 42k–65k
   tokens per cell against 15k for no-search. Native search reports 28k. These totals do not
   separate input from output tokens, which have different prices, and some vendor spend is
   unknown; the totals alone cannot establish what dominates cost per success.

## Failure dive

Two tasks were initially at 0/24 across all arms; a third mostly failed, and the judged SCA
task was mostly ungraded. All four exposed bench bugs. Each was confirmed by reading the
deliverables, and each is fixed in the bench fix:

| Task | As first graded | Graded with SEWBENCH-01 | Cause |
| --- | --- | --- | --- |
| `unanswerable-python4-ga-date` | 0/24 | 24/24 | The decline rejected **any** date, including "as of 2026-09-28" and other releases' dates. |
| `unanswerable-private-company-audited-arr` | 3/23 graded | 22/23 graded | The prompt asks for how circulating revenue figures were derived; any dollar figure failed. The 24th cell (Parallel) hit its budget before delivering, is not graded, and counts as a failure in the tables, so the per-task row reads 22/24. |
| `upstream-diagnosis-urllib3-libressl-import-error` | 0/24 | 24/24 | A URL-only check on a field every arm filled in prose; the URLs were in `evidence_urls`. |
| `list-build-sca-tool-shortlist` (judged) | mostly ungraded | graded | The judge sometimes fences its JSON; the `default` profile name also counted as an arm-identity leak. |

With the fixes, the competitive pass rate rose between 14 and 29 points per arm (no-search +14, Exa +29). It rose most for
search arms, which had been penalised for citing and explaining evidence. **The first reading of
this battery, that no-search was about as good as any provider, was an artifact of these bugs.**

Genuine failures that remain:

- **Egress pricing:** unavailable AWS intra-AZ cells, as above.
- **`change-detection-openapi-30-to-31`:** most misses omit `example→examples` from the breaking
  changes. OAS 3.1 *deprecates* `example` rather than removing it. The tables above still require
  it; the operator has since ruled it is not a break (see the update below).
- **Budget exhaustion:** 8 cells. Six are Firecrawl or Parallel on long list-building and enumeration tasks; Brave and Tavily have one each.
- **By design:** the two remaining `expected_fail` tasks (`unversioned-runtime-drift`,
  `unindexed-ordering-regression`) are 0/3 everywhere.

## Codex (partial)

The codex harness (gpt-6-sol) ran one competitive task on 2026-09-28 before its weekly quota ran
out:

- **Egress pricing:** native 2/3, no-search 1/3. Brave and Tavily went 0/3 on the token budget,
  reading 393k–448k tokens per cell.
- **Expected-fail tasks:** codex's search arms burned the full provider-call budget on
  `multi-hop-npm-semver-dependents` instead of declining.

A full codex battery waits for the quota reset (2026-10-04 12:52Z).

## Caveats

- **Sample size:** n=42 per arm on the competitive set. Adjacent arms are within each other's
  intervals.
- **Timing:** the two runs were about ten hours apart. Same harness, model, tasks and budgets.
- **Pricing:** vendor spend is unpriced for Exa and Parallel, and for Perplexity's `ask` tool.
  Their $/success counts model tokens plus metered calls only.
- **Grading:** the judged tasks use a single claude judge, so agreement is not measured.

## Update (2026-09-30): operator decisions

- **OpenAPI:** `example→examples` is a deprecation, not a break, so the task no longer requires
  it. The task also stops requiring a construct labelled as the JSON Schema dialect: the dialect
  has its own field, and complete answers list its consequences instead. Both changes are in
  the bench fix. On replay, the task goes
  from 7/24 to 22/24. The two remaining misses cite hosts outside the allowlist.
- **Codex native search** is priced at Brave's per-request rate as a proxy for its backing
  vendor. Claude native search keeps Anthropic's published rate.
- **Tokens and outcome rate are reported together.** Tokens per success come from
  the bench fix.

Competitive pass rates regraded with the corrected catalog at the recorded grader revision:

| arm | pass | tokens/success |
| --- | --- | --- |
| firecrawl | 38/42 (90%) | 61.5k |
| perplexity | 37/42 (88%) | 47.7k |
| tavily | 36/42 (86%) | 61.3k |
| exa | 35/42 (83%) | 65.0k |
| native | 33/42 (79%) | 36.3k |
| parallel-web | 33/42 (79%) | 69.8k |
| no-search | 32/42 (76%) | 20.9k |
| brave | 31/42 (74%) | 89.2k |

The historical `tokens/cell` column averages measured total-billable tokens over attempted
cells with measured usage; unknown or estimated usage is excluded, never treated as zero.
The update's `tokens/success` sums measured tokens over graded cells and divides by
measured successes, as defined by the bench fix; it excludes ungraded and unmeasured cells and is
not a full-arm lower bound. The per-arm measured coverage counts were not retained here. Multiplying a measured-cell mean by all 42 attempts need not reproduce it.
The previously quoted fresh/cache/output means are withdrawn: their cell coverage and
bucket accounting were not recorded here and they do not reconcile for Firecrawl, Parallel
or no-search. They cannot support a token-mix comparison from this document.

The output-reduction claim is also withdrawn. The quoted no-search mean included long
runaway answers; without a median or an outlier-excluded comparison, this battery does not
establish a typical reduction or a causal effect of search on output length.
Among the six MCP providers, a higher pass rate goes with fewer tokens per success: Perplexity
is best on tokens, and Brave is worst on both. Spearman ρ ≈ −0.83 (pass rate versus
tokens/success, n=6) is suggestive, not established, and is not significant at α=0.05.
No arm's pass-rate difference from no-search has an interval that
excludes zero. Firecrawl comes closest at +14 points (−2 to +30).

Read the [summary data](summary.json), [calibration record](calibration.json), [methodology](methodology.md) and [reproduction steps](reproduce.md).
