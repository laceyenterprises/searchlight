---
title: "Search-provider experiment: Stage B results"
doc_type: experiment
delegation: selective
influence_weight: low
staleness_window: 90d
kb_phase: build-out
canonical: false
status: partial
source_last_updated: 2026-09-26
last_updated: 2026-09-26
last_verified:
token_estimate: 13519
confidence_score: 0.66
provenance_reviewed: false
tags: ["experiments", "search-provider-eval", "results", "exa", "parallel", "firecrawl", "entity-lists", "schema-fill"]
provenance_summary: >-
  Stage B of the pre-registered search-provider experiment (Amendment 5), run 2026-09-26. Entity lists (S5) and
  schema fill (S6) ran on Exa, Parallel, and Firecrawl; find-similar (SP) and Monitors (SM) ran on Exa only. Seven
  Claude judge agents graded the outputs, blind to provider for S5 and S6 (SP can't be blind; only Exa ran). S5
  entities were checked on the page. S6 values were judged against a KB answer key: every value not judged `correct`
  had its cited page opened, and so did a seeded sample of the `correct` ones. Post-hoc choices are logged in
  Amendments 6 and 7; the second followed an adversarial review. Every table is generated from the data by
  `code/make_tables_b.py`. The Monitors probe runs until 2026-10-10. Internal only, small n.
---
> **Record note (Searchlight, 2026-10-06).** This file is the experiment's own record, copied from the private
> knowledge base where the experiment ran. Links between record files are converted to relative links; links to
> other knowledge-base pages, which are not published, are shown as plain text with the page's path. Nothing else is
> changed unless a further note says so. The Searchlight summary is [REPORT.md](../REPORT.md).
>
> **Further note.** The SM section below was written while the probe was running. The monitors were harvested
> and paused on 2026-10-06; the result is in [REPORT.md](../REPORT.md#monitors-probe-sm).

> [!summary] Quick Peek
> - Purpose: What Exa, Parallel, and Firecrawl added to this KB in Stage B, in hard numbers:
>   - S5, three entity lists;
>   - S6, filling a fixed 8-field competitor schema for 20 companies;
>   - SP, Exa's find-similar.
> - Headline:
>   - **Entity lists (S5): Exa found the most new entities.** 45 entities are new to the KB under the primary rule. Exa returned 28 of them, and 21 came from no other provider, for $2.02 ($0.072 per new entity).
>     - Firecrawl found 19 (12 unique), with the best precision (82.6%), on free daily runs.
>     - Parallel found 7 (5 unique), for $10.16 at list price ($1.45 each).
>     - Under the stricter entity-level rule it's 27, 18, and 3; the order doesn't change.
>   - **Schema fill (S6): Parallel matched the KB most often, and Exa was close behind at the same price.** Strictly right on 133 ground-truth cells:
>     - Parallel 113 and Exa 105. That gap isn't significant (company-level sign test p = 0.12).
>     - Firecrawl 94, clearly behind Parallel (p < 0.001) but not clearly behind Exa (p = 0.15).
>     - False-claim rates: Parallel 3.8%, Exa 4.7%, and Firecrawl 10.2% (7.8% without its values that don't answer the field). Citation validity doesn't separate the three.
>   - **New facts for the KB.**
>     - S6: 40 cells gained a verified new fact. 8 of its 19 corrections are substantive KB fixes; the other 11 are details, contested readings, vendor claims, or pages where the vendor contradicts itself.
>     - S5c: 28 companies whose own pages name Exa. The KB recorded none of them as doing so, though 4 appear in other roles.
> - Read this when: You're choosing a provider for list building or schema fill on this KB, or you're preparing the KB-facts PR.
> - Skip this when: You're writing external copy. The results are small-n, from one run date, and the owner decided they won't be published.
> - Key links: [Experiment overview](overview.md); [Stage A results](results-stage-a.md); [Pre-registration (Amendments 5–7)](preregistration.md); [Stage B judge brief](judge-brief-b.md)
> - Confidence: 0.66. Pre-registered sets and parameters, with blind judges for S5 and S6. Lowered for:
>   - small samples (3 lists, 20 companies) and one run per item;
>   - judges that are Claude agents, with blinding that relied on instructions;
>   - Firecrawl's free runs, and list-price costs for Parallel;
>   - the post-hoc choices in Amendments 6 and 7.

> [!warning] Internal only
> Stage B is one run per list and per company, all on 2026-09-26. It isn't a benchmark. The owner is a customer evaluating providers for its own use and decided the results won't be published. Parallel's terms restrict benchmark use (see the [README](overview.md)).

# Stage B results

## At a glance
> [!cite] Section provenance
> - Source basis: generated from `data/stage-b/` by `code/make_tables_b.py`, from the judges' verdicts rejoined to providers after judging, with the post-hoc adjustments of Amendments 6 and 7.
> - Confidence: 0.68

<!-- tables:glance_b:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| S5 entities verified and new to the KB (3 lists, fact-level rule) | 28 | 7 | 19 |
| … no other provider returned it | 21 | 5 | 12 |
| S5 precision: entities meeting every criterion | 28/36 (77.8%) | 13/21 (61.9%) | 19/23 (82.6%) |
| S6 right on KB ground truth, strict (of 133 cells) | 105/133 (78.9%) | 113/133 (85.0%) | 94/133 (70.7%) |
| S6 cells filled (of 160) | 149/160 (93.1%) | 156/160 (97.5%) | 157/160 (98.1%) |
| S6 false-claim rate | 7/149 (4.7%) | 6/156 (3.8%) | 16/157 (10.2%) |
| S6 cells where it added a verified new fact (of 40) | 30 | 31 | 25 |
| Stage B spend | $4.09 | $12.16 (list price) | 393 credits ($0.29–$1.97), plus 5 free runs |
<!-- tables:glance_b:end -->

- <!-- tables:s5_any:start -->
Across the three lists the pool held 70 distinct entities, and 45 of them were verified and new to the KB (fact-level rule; 42 as judged, 39 under the stricter entity-level rule).
<!-- tables:s5_any:end -->
- <!-- tables:s6_any:start -->
40 of the 160 cells gained a verified new fact from at least one provider: 19 corrections to a KB value and 21 cells the KB left empty. Of the corrections, 8 substantive, 4 detail, 3 contested, 2 vendor claim only, 2 vendor pages conflict (Amendment 7). Another 5 cells had verified values for facts the KB already states; those add a citation at most.
<!-- tables:s6_any:end -->

## Setup
> [!cite] Section provenance
> - Source basis: `code/sets/S5.json`, `S6.json`, `S6_truth.json`, `SP.json`, `SM.json`, `code/harness_b.py`, `code/config_b.json`, and [Amendments 5–7](preregistration.md) [observed — 2026-09-26].
> - Confidence: 0.85

**Sets.**

| Set | What it asks | Providers |
|---|---|---|
| S5a | Systems integrators and consultancies with an agentic-AI practice that have published a web-search integration for AI agents (113 known entities excluded) | Exa, Parallel, Firecrawl |
| S5b | Enterprise-search and workplace-AI products that also answer from the public web, and which web search provider powers them (45 excluded) | Exa, Parallel, Firecrawl |
| S5c | Companies whose own public pages name Exa as a subprocessor or as the search provider behind a feature (183 excluded) | Exa, Parallel, Firecrawl |
| S6 | 20 search, data, and agent-infrastructure companies × 8 fields: web search source, SOC 2, zero data retention, HIPAA BAA, official MCP server, free tier, list price, and last funding | Exa, Parallel, Firecrawl |
| SP | Find-similar on 10 competitor homepages | Exa |
| SM | Five Monitors on competitors' pricing and changelog pages, daily for two weeks | Exa |

**Parameters** (Amendment 5, from each vendor's docs, read 2026-09-26). Each provider got one shot, with no preview or
tuning runs.
- **Exa:**
  - S5: Agent at `effort: auto` (default $5 cap).
  - S6: Agent at `effort: medium`, $0.10 a company.
  - SP: `/findSimilar` with 10 results.
  - SM: `/monitors`, daily.
- **Parallel:**
  - S5: FindAll `core` with `match_limit: 20`, plus a `base` enrichment for the non-criteria fields.
  - S6: Task `pro`, $0.10 a company. S6 is price-matched with Exa.
- **Firecrawl:**
  - `/agent` with a JSON schema, at the default model.
  - S5: one run per list, capped at 2,500 credits.
  - S6: four runs of five companies, capped at 1,000 credits each. This batching was pre-registered and fits Firecrawl's per-run pricing, but it means each Firecrawl run covered five companies against one for the others.

**Inputs.** The task inputs were the same for every provider: each list's objective, criteria, fields, exclusions, and
20-item cap, and each company's name and homepage. The wrappers differ, per `code/harness_b.py`:
- **Exa got a system prompt in both sets.**
  - S5: "Return only entities you verified… Don't return entities from input.exclusion…".
  - S6: "A value must be supported by its cited page; otherwise return null". This may explain some of Exa's 11 nulls, which lower its completeness but also its false claims.
- **Parallel's S6 output schema marks every field required** (nullable). Exa's and Firecrawl's require only `company`.

**Grading** (the brief: [judge-brief-b.md](judge-brief-b.md)):
- **S5.**
  - Entities were pooled per list and deduplicated by registrable domain.
  - Each was judged against every criterion on a public page (`meets`, `partial`, `fails`, or `unverifiable`), and its
    claimed fields were checked.
  - Novelty was judged against the KB baseline (933777c). The judges applied two readings of the brief: entity-level
    in S5b, fact-level in S5c. Amendment 7 makes fact-level the rule and reports the others (see S5).
- **S6.** Every value in a cell was judged with its cited URL.
  - **The answer key.** `S6_truth.json` has 113 positive and 20 absence cells. It was built from the KB before any
    Stage B call, recorded at e560164, whose leaves are identical to 933777c.
  - **Verdicts.** `correct` means the value matches the key. A value that disagrees with the key and is verified on a
    current primary page is a `correction`. Cells without ground truth were judged `verified`, `partly_verified`,
    `wrong`, or `unverifiable`.
  - **Citations.** They were opened for every value not judged `correct`, for every value in cells without ground
    truth, and for a seeded blind sample of `correct` values (ids ending `.v1`). The other 182 of 462 values were
    judged only against the key.
- **SP.** Each new domain was classified as a relevant vendor, a profile or directory, or a clone or unrelated. It isn't
  blind, because only Exa ran.
- **Judges.** Seven Claude judge agents, working from shuffled pools, with provider names removed for S5 and S6. Their
  usage reports sum to about 2.40M tokens; the figure comes from the session's task notifications and isn't stored in
  the repo.

**Deviations from the pre-registration:**
- **Cap correction** (the Amendment 5 addendum). Parallel's cap was below its worst case, so the guard refused its S5c
  run. The cap was raised to $18.00, inside the $40 total, and S5c was re-run for Parallel alone.
- **Free runs.** Firecrawl's first five `/agent` runs a day are free. All three S5 runs and two of four S6 runs used
  them, so Firecrawl has no S5 cost signal.
- **Post hoc, Amendment 6 (after unblinding).**
  - Two same-company pairs that the domain dedupe missed (Sana and Coherence) were merged.
  - The KB baseline was checked for the facts in S6 cells without ground truth.
- **Post hoc, Amendment 7 (after the PR #25 review).** None of these changes a judge's verdict except one, which is
  noted.
  - One S6 verdict was downgraded: Diffbot's list price (Exa) went from correction to partly correct, because the
    judge's own note calls it a sales floor.
  - Four Firecrawl values that don't answer the field are flagged. They stay wrong in the pre-registered rate.
  - The S6 corrections are typed.
  - S5 novelty has one rule, and three exclusion flags are fixed.
  - The post-hoc KB check was corrected (Browserbase's MCP).
  - Tests and ranges were added.

## Cost and latency
> [!cite] Section provenance
> - Source basis: `data/stage-b/runs/ledger_b.json` and the run records [observed — 2026-09-26].
>   - Exa's dollars are the API's own `costDollars`.
>   - Parallel's are list-price estimates from the harness config, not billed amounts: FindAll core $2 a run plus $0.15 a match plus $0.01 a match for enrichment, and Task pro $0.10.
>   - Firecrawl's credits come from its account balance before and after each run. Dollars are a range, from its Scale plan ($0.00075 a credit) to the Hobby top-up ($0.005).
> - Confidence: 0.85

<!-- tables:b_cost:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| S5 entity lists (3 lists, one run each) | $2.02 | $10.16 (list price) | 0 credits (3 free daily runs) |
| S6 schema fill (20 companies × 8 fields) | $2.00 (20 runs) | $2.00 (20 runs, list price) | 393 credits ($0.29–$1.97 by plan): 2 paid runs of 5 companies, 2 free |
| SP find-similar (10 seeds) | $0.07 (10 calls) | — | — |
| SM Monitors (5, daily to 2026-10-10) | $15 per 1,000 runs; at most 70 runs ($1.05); billed at the end | — | — |
| Stage B total (ledger) | $4.09 | $12.16 | 393 credits ($0.29–$1.97 by plan) |
<!-- tables:b_cost:end -->

<!-- tables:b_total:start -->
At most $18.22 for Stage B and $5.07 for Stage A, or $23.29 of the owner's $40 cap. This values credits at the most they cost (Tavily $0.008, Firecrawl $0.005), as the cap guard did; at Firecrawl's Scale rate the total is $19.52. The Monitors probe adds at most $1.05 by 2026-10-10. The guard refused 1 run, Parallel's S5c, before the cap correction; it ran after.
<!-- tables:b_total:end -->

- **Exa Agent, S5.** One list cost $0.57–$0.86: 29–47 searches and 4.0–6.2 agent compute units each. Every S6 row cost
  $0.10, which was 12 searches and 0.4 compute units.
- **Parallel FindAll, S5.** It evaluated 74–84 candidates per list and matched 2, 17, and 7. Two runs stopped on a low
  match rate and one on exhausted candidates. The $2 fixed fee per run is most of the cost on short lists. FindAll
  `base` ($0.25 a run plus $0.03 a match) would have cost about $1.79 at the same match counts, but it wasn't run and
  would likely match differently.
- **Firecrawl `/agent`, S6.** The two paid runs of five companies used 226 and 167 credits, well under their
  1,000-credit caps.
- **Latency.** Measured by this harness, from one machine, and rounded up by each harness's poll interval (Exa 12 s,
  Parallel 15 s, Firecrawl 35 s). It isn't a latency benchmark:

<!-- tables:b_latency:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| S5, one list (range over 3 runs) | 122–146 s | 305–472 s | 72–107 s |
| S6, per company (median; range) | 49 s (37–73 s) | 76 s (61–561 s) | 14–21 s (runs of five companies took 72–107 s) |
| SP, one find-similar call (median) | 328 ms | — | — |
<!-- tables:b_latency:end -->

## S5: entity lists
> [!cite] Section provenance
> - Source basis: the S5 judge files, pools, attribution, and `S5_posthoc.json` (Amendment 7) in `data/stage-b/grading/b/`, scored by `code/score_b.py`.
> - "Returned" counts distinct entities after the domain dedupe. Parallel's raw count is higher where several of its matches were products at one company.
> - Confidence: 0.70

**Novelty rule (Amendment 7).**
- **Fact-level, the primary rule.** An entity is new when the KB's leaves don't record the fact the list asks for:
  that the product answers from the web, and with what (S5b), or that the company names Exa (S5c).
- **Entity-level, a sensitivity.** An entity is new only when the company appears nowhere in the KB's leaves.
- **Neither rule counts `_scratch/` research notes** as the KB.

The rule changes the counts but not the order:

<!-- tables:s5_rules:start -->
| Novelty rule | Exa | Parallel | Firecrawl | Any provider |
|---|---|---|---|---|
| Fact-level (primary): the KB's leaves don't record the fact the list asks for | 28 (21 unique) | 7 (5 unique) | 19 (12 unique) | 45 |
| As judged: S5b judged entity-level, S5c fact-level | 27 (20 unique) | 5 (3 unique) | 19 (12 unique) | 42 |
| Entity-level: the company appears nowhere in the KB's leaves | 27 (20 unique) | 3 (1 unique) | 18 (11 unique) | 39 |
| Spend per new entity, fact-level (entity-level) | $0.072 ($0.075) | $1.45 ($3.39) | free runs | — |
<!-- tables:s5_rules:end -->

### S5a: systems integrators with a published web-search integration
<!-- tables:s5_S5a:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| Entities returned (distinct by domain) | 6 | 2 | 3 |
| … with a homepage URL (raw) | 6/6 | 2/2 | 3/3 |
| Meet every criterion (precision) | 1/6 (16.7%) | 0/2 (0.0%) | 0/3 (0.0%) |
| Partial / fail / unverifiable | 4 / 1 / 0 | 2 / 0 / 0 | 3 / 0 / 0 |
| Returned despite the exclusion list | 0 | 1 | 0 |
| Verified and new to the KB (fact-level rule) | 1 | 0 | 0 |
| … no other provider returned it | 1 | 0 | 0 |
<!-- tables:s5_S5a:end -->

<!-- tables:s5_S5a_pool:start -->
Pooled: 11 distinct entities; 1 verified and new to the KB from any provider (fact-level rule; 1 as judged, 1 under the entity-level rule).
<!-- tables:s5_S5a_pool:end -->

- **This was the hardest list for every provider.** Most entities were `partial`: the firm had an agentic practice,
  but its "web search integration" was a tech-stack logo or a service pitch, not a published build.
- **Only one entity met every criterion:** Chaos Gears, an AWS partner with a published build guide for Tavily. Exa
  found it [extracted — 2026-09-26](https://chaosgears.com/blog/amazon-bedrock-agents-tavily-search-guide).

### S5b: enterprise-search and workplace-AI products that answer from the web
<!-- tables:s5_S5b:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| Entities returned (distinct by domain) | 13 | 12 (of 17 raw) | 5 |
| … with a homepage URL (raw) | 13/13 | 17/17 | 5/5 |
| Meet every criterion (precision) | 11/13 (84.6%) | 10/12 (83.3%) | 5/5 (100.0%) |
| Partial / fail / unverifiable | 2 / 0 / 0 | 2 / 0 / 0 | 0 / 0 / 0 |
| Returned despite the exclusion list | 0 | 6 | 0 |
| Verified and new to the KB (fact-level rule) | 11 | 5 | 5 |
| … no other provider returned it | 8 | 3 | 2 |
<!-- tables:s5_S5b:end -->

<!-- tables:s5_S5b_pool:start -->
Pooled: 25 distinct entities (26 before the post-hoc merge); 16 verified and new to the KB from any provider (fact-level rule; 13 as judged, 14 under the entity-level rule).
<!-- tables:s5_S5b_pool:end -->

- **Parallel returned mostly the head of the market.**
  - Six of its 12 companies were on the exclusion list: Microsoft, Google, Glean, Perplexity, Anthropic, and ChatGPT
    Enterprise. The pool's matcher also flagged GitHub Copilot by mistake, through a github.com URL, and missed
    ChatGPT Enterprise; Amendment 7 fixes both flags.
  - Two of its products are new under the fact-level rule:
    - Snowflake's Cortex Agents web search, which runs on Brave [extracted — 2026-09-26](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-manage);
    - Grok Business [extracted — 2026-09-26](https://x.ai/news/grok-business).
- **Exa and Firecrawl returned smaller vendors the KB lacks.** Examples are Kore.ai, CustomGPT.ai, DocsBot, soev.ai,
  Ejento, and Omnifact. Most name no web search provider on their own pages. Omnifact's DPA names Brave
  [extracted — 2026-09-26](https://omnifact.ai/dpa). All three providers found Guru and Moveworks.

### S5c: companies that name Exa on their own pages
<!-- tables:s5_S5c:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| Entities returned (distinct by domain) | 17 | 7 | 15 |
| … with a homepage URL (raw) | 10/17 | 7/7 | 15/15 |
| Meet every criterion (precision) | 16/17 (94.1%) | 3/7 (42.9%) | 14/15 (93.3%) |
| Partial / fail / unverifiable | 0 / 1 / 0 | 0 / 4 / 0 | 0 / 1 / 0 |
| Returned despite the exclusion list | 0 | 2 | 0 |
| Verified and new to the KB (fact-level rule) | 16 | 2 | 14 |
| … no other provider returned it | 12 | 2 | 10 |
<!-- tables:s5_S5c:end -->

<!-- tables:s5_S5c_pool:start -->
Pooled: 34 distinct entities (35 before the post-hoc merge); 28 verified and new to the KB from any provider (fact-level rule; 28 as judged, 24 under the entity-level rule).
<!-- tables:s5_S5c_pool:end -->

- **Exa and Firecrawl complemented each other.** They overlapped on only 4 of the 28 new companies. Parallel's two
  came from no one else: Novita, which resells Exa [extracted — 2026-09-26](https://docs.novita.ai/api-reference/model-apis-exa-search),
  and Apollo.io.
- **This list is about Exa itself**, so Exa could have had a home advantage. Firecrawl's near-parity (14 against 16)
  argues against a large one. Exa's own output also had a gap: 7 of its 17 entities came back with no homepage URL,
  which caused the Coherence dedupe miss.
- **Notable names.** The KB didn't record any of these as naming Exa:
  - **Apollo.io** lists Exa among its AI providers [extracted — 2026-09-26](https://www.apollo.io/ai-policy). The KB
    has Apollo only as a competitor.
  - **RingCentral** lists Exa for its AI Representative [extracted — 2026-09-26](https://www.ringcentral.com/legal/dpa-subprocessor-list.html).
  - **Gamma** lists Exa.ai for search [extracted — 2026-09-26](https://gamma.app/subprocessors). The KB has Gamma
    only as a Firecrawl logo.
  - **Coherence, Kolo, Kapso, and about twenty smaller AI products** list Exa as a subprocessor.
- **False positives:**
  - Uphold lists a different "Exa Labs", a Uruguayan fintech [extracted — 2026-09-26](https://uphold.com/en-eu/legal/uphold-subprocessor-list).
  - The current lists of Fireflies and Foundable don't name Exa.
  - Portkey and Google Cloud integrate Exa with the customer's own key [extracted — 2026-09-26](https://portkey.ai/docs/integrations/plugins/exa),
    which fails the "provider behind a feature" criterion.

### All three lists
<!-- tables:s5_total:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| Entities returned, three lists (distinct by domain) | 36 | 21 (of 26 raw) | 23 |
| … with a homepage URL (raw) | 29/36 | 26/26 | 23/23 |
| Meet every criterion (precision) | 28/36 (77.8%) | 13/21 (61.9%) | 19/23 (82.6%) |
| Returned despite the exclusion list | 0 | 9 | 0 |
| Verified and new to the KB (fact-level rule) | 28 | 7 | 19 |
| … no other provider returned it | 21 | 5 | 12 |
| S5 spend | $2.02 | $10.16 (list price) | 0 credits (free runs) |
| Spend per verified new entity | $0.072 | $1.45 | not measurable (free runs) |
<!-- tables:s5_total:end -->

**Claimed fields**, checked on entities only one provider returned, so each verdict belongs to one provider. The
table shows correct ÷ checked.

<!-- tables:s5_fields:start -->
| List | Field | Exa | Parallel | Firecrawl |
|---|---|---|---|---|
| S5a | `practice` | 1/6 | 2/2 | 3/3 |
| S5a | `web_search_providers` | 6/6 | 2/2 | 3/3 |
| S5b | `web_search_provider` | 11/11 | 9/10 | 3/3 |
| S5c | `exa_role` | 13/14 | 5/6 | 11/11 |
| S5c | `page_date` | 9/14 | 1/6 | 9/11 |
<!-- tables:s5_fields:end -->

The `page_date` field (the date on the page naming Exa) was the least reliable claim. The judges often found no date
on the page, or a different one.

## S6: schema fill (20 companies × 8 fields)
> [!cite] Section provenance
> - Source basis: the S6 judge files, pool, attribution, and post-hoc files in `data/stage-b/grading/b/` (`S6_nogt_kb_check.json` from Amendment 6; `S6_overrides.json` and `S6_correction_types.json` from Amendment 7), scored by `code/score_b.py`.
> - Confidence: 0.68

<!-- tables:s6_main:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| Cells filled (of 160) | 149/160 (93.1%) | 156/160 (97.5%) | 157/160 (98.1%) |
| Ground-truth cells answered (of 133) | 125 | 130 | 131 |
| … match the KB | 93 | 101 | 85 |
| … verified corrections to the KB | 12 | 12 | 9 |
| … partly correct | 14 | 12 | 23 |
| … wrong | 6 | 5 | 14 |
| Right, strict: match or correction (of 133) | 105/133 (78.9%); 106 as judged | 113/133 (85.0%) | 94/133 (70.7%) |
| Right, strict, of the ground-truth cells it answered | 105/125 (84.0%) | 113/130 (86.9%) | 94/131 (71.8%) |
| Right, broad: adds partly correct (of 133) | 119/133 (89.5%) | 125/133 (94.0%) | 117/133 (88.0%) |
| Cells without ground truth answered (of 27) | 24 | 26 | 26 |
| … verified / partly / wrong / unverifiable | 23 / 0 / 1 / 0 | 24 / 1 / 1 / 0 | 21 / 3 / 2 / 0 |
| False-claim rate: wrong ÷ values given | 7/149 (4.7%) | 6/156 (3.8%) | 16/157 (10.2%) |
| … leaving out values that don't answer the field | 7/149 (4.7%) | 6/156 (3.8%) | 12/153 (7.8%) |
| Cited page supports the value, values not judged wrong (of those checked) | 81/81 (100.0%) | 83/85 (97.6%) | 82/85 (96.5%) |
| Values judged wrong whose cited page the judge marked valid | 5 | 3 | 4 |
| Cells where it added a verified new fact (40 cells had one) | 30 | 31 | 25 |
| … and no other provider did | 4 | 3 | 3 |
| Substantive corrections (of the correction values) | 7 of 12 | 6 of 12 | 6 of 9 |
<!-- tables:s6_main:end -->

**Significance.** The values cluster by run: one Exa or Parallel run per company, and one Firecrawl run per five. So
the company-level sign tests are the ones to trust, and the cell- and value-level ones overstate precision.

<!-- tables:s6_paired:start -->
Paired sign tests on the 133 ground-truth cells (strict; two-sided, uncorrected for the three comparisons): Parallel vs Exa: by cell 21–13 (p = 0.23); by company 11–4 with 5 ties (p = 0.12); Exa vs Firecrawl: by cell 25–14 (p = 0.11); by company 9–3 with 8 ties (p = 0.15); Parallel vs Firecrawl: by cell 28–9 (p = 0.003); by company 18–2 with 0 ties (p < 0.001). The by-company tests respect how values cluster within one run.
<!-- tables:s6_paired:end -->

<!-- tables:s6_fisher:start -->
Fisher exact tests on values (two-sided, uncorrected): Parallel vs Exa: completeness p = 0.11, false-claim rate p = 0.78, citation validity (not wrong) p = 0.50; Exa vs Firecrawl: completeness p = 0.05, false-claim rate p = 0.08, citation validity (not wrong) p = 0.25; Parallel vs Firecrawl: completeness p = 1.00, false-claim rate p = 0.04, citation validity (not wrong) p = 1.00.
<!-- tables:s6_fisher:end -->

- **Citation validity doesn't separate the providers.** Most invalid citations were on values already scored `wrong`,
  which the brief defines as unsupported by the cited page. On values not judged wrong, all three are 96–100%, with no
  significant differences. Some values judged wrong still had a valid citation: the page existed and said something,
  but not the fact.
- **Empty cells count as unanswered.** The set's instructions asked for null when a value couldn't be verified, and
  for an explicit "none found", with the pages checked, when something is absent.
  <!-- tables:s6_empty:start -->
Cells left empty, by the answer key's kind: Exa 11 (3 absence, 3 no ground truth, 5 positive); Parallel 4 (2 absence, 1 no ground truth, 1 positive); Firecrawl 3 (1 no ground truth, 2 positive).
<!-- tables:s6_empty:end -->
- **Most of Firecrawl's errors were in one field.** Web search source had 5 of its 16 wrong values. Four of those
  described the product without naming what powers it, and they're flagged as not answering the field.
- **Corrections to the KB came from every provider, and fewer than half are substantive.**
  <!-- tables:s6_corr_overlap:start -->
10 of the 19 correction cells were found by two or three providers, and 9 by one. 8 of those 9 single-provider corrections aren't substantive.
<!-- tables:s6_corr_overlap:end -->
  - Each correction is typed in the appendix. The soft ones are details, contested readings, vendor claims, or
    conflicting vendor pages.

**By field**:
<!-- tables:s6_fields:start -->
Each cell: values judged right (match, correction, or verified) ÷ values given, and the wrong count.

| Field | Ground-truth cells | Exa | Parallel | Firecrawl |
|---|---|---|---|---|
| SOC 2 | 14 of 20 | 18/19 | 18/20, 1 wrong | 17/20, 1 wrong |
| HIPAA BAA | 14 of 20 | 14/16, 2 wrong | 19/20 | 17/20, 3 wrong |
| Zero data retention | 13 of 20 | 17/19, 1 wrong | 14/20, 2 wrong | 13/20, 2 wrong |
| Official MCP server | 17 of 20 | 20/20 | 20/20 | 18/20, 1 wrong |
| Free tier | 17 of 20 | 16/20, 2 wrong | 18/19 | 18/20, 1 wrong |
| Last funding | 18 of 20 | 15/19, 2 wrong | 16/19, 1 wrong | 11/19, 2 wrong |
| List price | 20 of 20 | 15/19 | 19/20 | 12/18, 1 wrong |
| Web search source | 20 of 20 | 13/17 | 13/18, 2 wrong | 9/20, 5 wrong |
<!-- tables:s6_fields:end -->

**Cost per result**:
<!-- tables:s6_cost:start -->
| | Exa | Parallel | Firecrawl |
|---|---|---|---|
| Values judged right (match, correction, or verified) | 128 | 137 | 115 |
| S6 spend | $2.00 | $2.00 | 393 credits for 10 of 20 companies |
| Spend per company | $0.10 | $0.10 | 39 credits ($0.03–$0.20 by plan) on the paid runs |
| Spend per right value | $0.0156 | $0.0146 | not measurable (half the rows ran free) |
<!-- tables:s6_cost:end -->

## SP: find-similar (Exa only)
> [!cite] Section provenance
> - Source basis: `data/stage-b/runs/SP/exa.jsonl`, `data/stage-b/grading/SP_domains.json`, and the SP judge file (not blind, since only Exa ran).
> - Confidence: 0.70

<!-- tables:sp:start -->
| Exa find-similar, 10 competitor homepages | Count |
|---|---|
| Results returned | 100 |
| Distinct registrable domains | 69 |
| … already cited in the KB | 8 |
| … new to the KB | 61 |
| New domains: relevant vendors (search, SERP, crawling, or web-data APIs) | 11 |
| New domains: profiles, directories, or reviews of a seed company | 39 |
| New domains: clones or unrelated | 11 |
| Relevant vendors the KB baseline already covers | 0 |
| Spend | $0.07 |
<!-- tables:sp:end -->

Find-similar is a cheap long-tail sweep, but it needs triage: 50 of the 61 new domains were directories, profiles,
clones, or unrelated. The 11 vendors are small SERP or web-data APIs, worth a line in the competitor long tail rather
than new leaves. Two need care:
- serpapi.org is a SerpApi lookalike that redirects to a separate vendor.
- The judge rated serpermatrix.com as low credibility.

## SM: Monitors (Exa only; running)
> [!cite] Section provenance
> - Source basis: `data/stage-b/runs/SM/exa.jsonl` and `code/sets/SM.json`.
> - Confidence: 0.80

<!-- tables:sm:start -->
5 monitors were created on 2026-09-26 after 5 first attempts were refused (HTTP 400: Exa rejects example.com as a webhook). They run daily until 2026-10-10. Results are polled through the API, graded, and written up then; the monitors are deleted on that date.
<!-- tables:sm:end -->

The pre-registered question: does a monitor surface a real, dated pricing or product change on the competitor's own
page within the KB's 30-day staleness window? It's graded yes or no, and against whether the change was already in the
KB.

## What Stage B says [inferred]
1. **For list building, Exa found the most new material, at the lowest measured cost.**
   - 28 of the 45 new entities came from Exa, and 21 from Exa alone, at $0.072 per new entity.
   - The stricter entity-level rule gives 27, 18, and 3 and keeps the order.
   - Firecrawl adds real coverage (12 unique entities) and had the best precision. It ran on free daily runs, so its
     cost can't be compared.
   - <!-- tables:s5_union:start -->
Exa and Firecrawl together found 40 of the 45 new entities; 5 came from Parallel alone, 21 from Exa alone, and 12 from Firecrawl alone. 7 were found by more than one provider.
<!-- tables:s5_union:end -->
2. **Parallel FindAll was the most expensive per new entity, and it leaned to the head of the market.**
   - $10.16 at list price bought 7 new entities under the fact-level rule, and 3 under the entity-level rule.
   - 9 of its 21 companies were on the exclusion lists it was sent.
   - This is one run per list at `core`. FindAll's `base` and `pro` generators weren't tested.
3. **For schema fill on known companies, Parallel and Exa are close at the same price.**
   - Parallel filled more cells (156 against 149) and was right on 8 more ground-truth cells. Neither gap is
     significant (company-level p = 0.12, completeness p = 0.11).
   - Firecrawl was behind Parallel (p < 0.001 by company) and not clearly behind Exa (p = 0.15).
   - Only price-matched tiers were compared: Exa `medium` and Parallel `pro`. Exa's `high` and `xhigh` efforts and
     Parallel's `ultra` processors weren't run.
4. **The KB had some stale cells, and the providers caught them.** 8 of the 133 ground-truth cells (6%) got a
   substantive correction:
   - Perplexity's API-wide ZDR;
   - Firecrawl's entry price;
   - You.com's SOC 2 type;
   - Valyu's funding;
   - Jina's USD price;
   - the Glean and Browserbase BAAs;
   - Apify's lead investor.

   11 more rest on details, contested readings, vendor claims, or pages where the vendor contradicts itself. The
   KB-facts PR should carry the first group as corrections and label the rest for what they are.
5. **The biggest GTM finding is S5c.** 28 companies name Exa on their own pages, and the KB recorded none of them as
   doing so. They include Apollo.io, RingCentral, and Gamma.

## Caveats
> [!cite] Section provenance
> - Source basis: this page, the pre-registration (Amendments 5–7), and the PR #25 Stage B review.
> - Confidence: 0.80

- **Sample.** Three lists and 20 companies, one run each, on one date. One judge per chunk, and the judges are Claude
  agents.
- **Blinding relied on instructions.** The attribution maps sat in `grading/b/_attrib/`, inside the folder the judges
  read from, and the brief was finalized within a minute of their being built. The seeded `.v1` citation sample came out
  balanced: 34–36% of each provider's `correct` values. Next time, write attribution outside the judges' tree and
  hash the brief before any pool exists.
- **Costs.**
  - Parallel's dollars are list-price estimates.
  - Firecrawl's S5 ran free, and nobody tested whether free and paid runs behave differently. Its credits are shown as
    a range across plans.
  - The untested tiers run both ways: Exa's higher efforts, and Parallel's FindAll `base`/`pro` and higher Task
    processors.
- **Answer key.**
  - `correct` means the value matches the KB, and 182 of the 462 values were never opened on a page.
  - The `correction` verdict catches stale KB values only where the judge verified the new value on a page.
  - The key's builder missed no tagged fact after all: the Browserbase MCP line that Amendment 6 cited is about
    Kernel. Amendment 7 corrects this, which adds one new-fact cell for all three providers.
  - The builders of the key and of the exclusion lists aren't committed. The key is traceable through its `kb_line`
    fields.
- **Clustering.** Value-level tests overstate precision, so read the company-level sign tests. None of the tests is
  corrected for the three comparisons. Parallel vs Firecrawl survives a Bonferroni correction; nothing else is
  significant.
- **Dedupe and exclusion matching.**
  - Registrable-domain dedupe merges distinct products at one company: Parallel's 17 S5b matches became 12
    companies.
  - It also missed two same-company pairs, which were merged after unblinding. That lowered Exa's and Firecrawl's
    unique counts by 2 each.
  - Matching exclusions by domain is fragile on shared hosts: the review counted 16 S5c exclusion keys on github.com. `grade_prep_b.py` now skips shared hosts and also matches by name.
- **Novelty.** For S6's empty cells, the post-hoc check read each company's own leaf and no other leaf.
- **Judge notes.** They paraphrase and may quote short fragments (under 15 words) of provider values or pages. The
  review counted 13 of the 462 S6 notes sharing 6–8 consecutive words with the provider's value.

## Appendix: verified findings (for the KB-facts PR)
> [!cite] Section provenance
> - Source basis: the Stage B judge files. The text is the judge's paraphrase, and "Verified on" is the page where the judge confirmed it. Social posts aren't linked here; they're in the grading data. Findings go to canonical leaves in a separate PR, cited to the primary page.
> - Confidence: 0.75

### S6 corrections to KB values (19 cells)
"Type" follows Amendment 7:
- `substantive`: the KB was wrong or stale, or missed a material fact.
- `detail`: a true refinement of a value that was right.
- `contested`: the reading is disputable.
- `vendor claim only`.
- `vendor pages conflict`: the vendor's own pages disagree.

<!-- tables:findings_s6_corrections:start -->
| Company | Field | Type | What the verified value changes (judge's paraphrase) | Verified on | Credited |
|---|---|---|---|---|---|
| Tavily | Last funding | detail | Verified in Nebius's interim statements for the period ended 2026-06-30: acquired Feb 19, 2026; total fair value $189.4M ($177.0M cash, $10.0M vested holdback, $2.4M equity) after a $0.3M Q2 measurement-period reduction. The… | [sec.gov](https://www.sec.gov/Archives/edgar/data/1513845/000110465926094844/nbis-20260812xex99d2.htm) | Parallel |
| Tavily | SOC 2 | detail | Verified on the rendered trust center: SOC 2 Type II, with a resource titled 'Tavily - SOC2 Type II - 2026'. The KB's 'no report date stated' is stale, because the trust center dates the 2026 report (update posted 2026-06-17). | [tavily.com](https://trust.tavily.com/) | Parallel, Firecrawl |
| Perplexity (Search API) | HIPAA BAA | contested | The API Terms (updated 2026-01-23, section 6.2) permit PHI only once Customer and Perplexity have executed a BAA, and the Enterprise Terms (section 5.2) say the same. The KB's 'no BAA stated' misses this contractual BAA path,… | [perplexity.ai](https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service) | Exa |
| Perplexity (Search API) | Last funding | detail | Perplexity's post (Dec 4, 2025) says Ronaldo "is now also an investor in Perplexity", amount undisclosed. That's later than the KB's Sept 2025 round, and Tracxn lists it as the latest round (Angel). The KB leaves it out.… | [perplexity.ai](https://www.perplexity.ai/hub/blog/perplexity-x-cristiano-ronaldo) | Exa, Parallel |
| Perplexity (Search API) | SOC 2 | detail | Verified: the post (May 13, 2026) says Perplexity's infrastructure "completed its 2026 SOC 2 Type II attestation". The KB says no date is stated, but the 2026 attestation year is public. | [perplexity.ai](https://www.perplexity.ai/hub/blog/how-we-built-security-into-computer) | Parallel |
| Perplexity (Search API) | Zero data retention | substantive | The FAQ covers the whole Perplexity API, Search included: "We do not retain any query data sent through the API", and it says zero-day retention is the API default. The KB limits ZDR to Chat Completions, following the privacy… | [perplexity.ai](https://docs.perplexity.ai/docs/resources/faq) | Exa, Firecrawl |
| Firecrawl | List price | substantive | Verified on the pricing page (effective 2026-09-04). Search costs 2 credits per 10 results, and Hobby pay-as-you-go adds 1,000 credits per $5, so $10 per 1,000 searches at the entry rate. The KB's ~$5 per 1,000 is third-party… | [firecrawl.dev](https://www.firecrawl.dev/pricing) | Exa, Parallel |
| You.com | SOC 2 | substantive | The KB has the type unstated (from /business) and says the trust center didn't render. Rendered in a browser, it lists 'SuSea, Inc. - 2025 SOC 2 Type 2 Report' (access on request; SuSea is You.com's entity). The Security… | [you.com](https://trust.you.com/resources) | Exa, Parallel, Firecrawl |
| Valyu | Last funding | substantive | The KB says none found. PitchBook's public profile (rendered in a browser, no login) shows one Accelerator/Incubator deal: $500K on 26-Mar-2024, investor Andreessen Horowitz. a16z crypto's own posts confirm it: Valyu Network… | [pitchbook.com](https://pitchbook.com/profiles/company/592911-01) | Exa, Parallel, Firecrawl |
| Valyu | SOC 2 | vendor claim only | The KB says no SOC 2 was found. The pricing page's Enterprise block shows 'SOC 2 TYPE 2' (linked to the AICPA): 'Independently audited controls for security, availability and confidentiality'. The same badge is in the… | [valyu.ai](https://www.valyu.ai/pricing) | Firecrawl |
| Nimble | Web search source | vendor claim only | The KB says there's no stored index. Nimble's pricing page (and site-wide header) claims 'a Proprietary Index and Memory that gets smarter with every query', alongside live-web search. This is a vendor claim; the Search docs… | [nimbleway.com](https://www.nimbleway.com/pricing) | Exa, Parallel |
| SerpApi | Zero data retention | vendor pages conflict | Verified: the rendered plan table (and its plan data) ticks ZeroTrace Mode on the self-serve Cloud 1M to Cloud 54M plans ($3,750/month and up), not on Infrastructure or below. The KB's 'Enterprise only' is incomplete.… | [serpapi.com](https://serpapi.com/pricing#all-plans) | Parallel |
| Jina AI | List price | substantive | The rendered page (fed by Jina's public product API) shows 1B tokens for $50 (0.050 per 1M) and 11B for $500 (0.045 per 1M). The KB leaves the USD rate uncaptured. At Search's 10,000-token minimum per request this is about… | [jina.ai](https://jina.ai/contact-sales/) | Firecrawl |
| Jina AI | Zero data retention | contested | Jina's live blog (Jan 31, 2025; the cited /it/ page is a translation) lists for its API a 'Zero data retention policy - we don't store or log your requests'. The current Reader page also offers a per-request 'Do Not Cache or… | [jina.ai](https://jina.ai/news/a-practical-guide-to-deploying-search-foundation-models-in-production/) | Exa |
| Glean | HIPAA BAA | substantive | KB lists HIPAA but doesn't say whether Glean signs a BAA. Legal page lists BAAs with DocuSign signing forms; no plan stated. 'Signing process not specified' is inaccurate given those links. | [glean.com](https://www.glean.com/legal) | Exa, Parallel |
| Glean | Zero data retention | contested | KB has only web-search ZDR and no Glean-wide offer. Protect overview lists zero LLM data retention (no LLM training on enterprise data) for every deployment; Core Suite pricing includes Protect in the seat fee. Omits the… | [glean.com](https://docs.glean.com/administration/protect/overview) | Firecrawl |
| Browserbase | HIPAA BAA | substantive | KB says no BAA is stated. The healthcare page FAQ says BAAs are available for Enterprise plan customers, and the plans doc now shows 'BAA Available' on Scale. | [browserbase.com](https://www.browserbase.com/use-case/browser-automation-for-healthcare) | Exa, Parallel, Firecrawl |
| Diffbot | Free tier | vendor pages conflict | KB has 10,000 credits at 1 credit per search. The Web Search API docs give a free token 100,000 queries a month at 60 QPM. Diffbot's pricing page still shows 10,000 credits, so its pages conflict. | [diffbot.com](https://www.diffbot.com/docs/web-search/) | Exa |
| Apify | Last funding | substantive | KB names no lead and calls the round unconfirmed by Apify. The CEO's public LinkedIn post (2024-04-15) confirms about $3M from new investor J&T Ventures, with Reflex Capital participating. The cited www.ain.ua URL 404s; the… | [ain.ua](https://en.ain.ua/2024/04/15/apify-secures-2-8m-investment/) | Exa, Parallel, Firecrawl |
<!-- tables:findings_s6_corrections:end -->

### S6 facts the KB didn't have (21 cells)
"Absence" means the fact is that the company offers no such thing, verified on its own pages.

<!-- tables:findings_s6_new:start -->
| Company | Field | Verified fact (judge's paraphrase) | Type | Verified on | Credited |
|---|---|---|---|---|---|
| SerpApi | HIPAA BAA | Accurate: the only HIPAA mention is on the ZeroTrace page, saying the mode can ease compliance. No BAA offer is found. | absence | [serpapi.com](https://serpapi.com/security) | Exa, Parallel, Firecrawl |
| SerpApi | Last funding | The Aug 19, 2026 release covers 1.5M activated accounts and mentions no financing. SerpApi's CFO job post says it has 'no outside investors'. | positive | [prnewswire.com](https://www.prnewswire.com/news-releases/serpapi-surpasses-1-5-million-activated-user-accounts-empowering-developers-everywhere-with-structured-access-to-the-worlds-search-data-302855745.html) | Exa, Firecrawl |
| SerpApi | SOC 2 | Security page lists SOC 2 (Type II) as 'Acquired' with no report date. The blog post of 2026-05-29 announces the completed SOC 2 Type 2 examination. | positive | [serpapi.com](https://serpapi.com/security) | Exa, Parallel |
| Serper | Official MCP server | No MCP mention on serper.dev (/mcp is 404). GitHub search finds only user-owned wrappers (e.g., [user]/mcp-server-serper), and there is no serper-dev org. | absence | [serper.dev](https://serper.dev/) | Exa, Parallel, Firecrawl |
| Kagi | HIPAA BAA | No HIPAA or BAA on the privacy, security, teams or API pages; the API section asks users not to send sensitive personal data. | absence | [kagi.com](https://kagi.com/privacy) | Exa, Parallel, Firecrawl |
| Kagi | SOC 2 | Security page cites an independent Illumant audit only. No SOC 2 on Kagi's pages or in web search. | absence | [kagi.com](https://help.kagi.com/kagi/privacy/security.html) | Exa, Parallel, Firecrawl |
| Kagi | Zero data retention | Policy (updated 2026-09-22) has an API section: queries may be logged temporarily, unlinked from the account; load-balancer and VM logs 7 days, sampled Sentry 90 days. No ZDR option. | absence | [kagi.com](https://kagi.com/privacy) | Exa, Parallel, Firecrawl |
| Bright Data | Zero data retention | Privacy policy (redirects to /privacy) and trust center offer no ZDR; consistent with Bright Data's own 'Not available'. | absence | [brightdata.com](https://brightdata.com/legal/privacy) | Exa, Parallel |
| Glean | Free tier | Core Suite is licensed per user per month, with no free tier. No free tier or trial on glean.com, the demo page, or developers.glean.com; glean.com/pricing redirects to the homepage. | absence | [glean.com](https://docs.glean.com/glean-core-suite-pricing) | Exa, Parallel, Firecrawl |
| Clay | HIPAA BAA | Trust Center compliance lists SOC 2 Type 2, GDPR, CCPA, ISO 27001, and ISO 42001. Resources and FAQ have no HIPAA or BAA; web search found none. | absence | [clay.com](https://trust.clay.com/) | Exa, Parallel, Firecrawl |
| Clay | SOC 2 | Trust Center lists an undated SOC 2 Type 2 Report and a SOC 2 Bridge Letter dated September 2026. | positive | [clay.com](https://trust.clay.com/) | Exa, Parallel, Firecrawl |
| Clay | Zero data retention | Privacy page has only standard purpose-based retention; the Trust Center and its FAQ list no ZDR. | absence | [clay.com](https://www.clay.com/privacy) | Parallel, Firecrawl |
| Browserbase | Official MCP server | Blog says Browserbase first released its MCP server in November 2024 and describes Stagehand MCP. Current docs call it the Browserbase MCP server. | positive | [browserbase.com](https://www.browserbase.com/blog/ai-web-agent-sdk) | Exa, Parallel, Firecrawl |
| Browserbase | Zero data retention | Doc: under ZDR, session logs and replay aren't persisted; downloads, uploads, contexts, and extensions are separate (pair with BYOS). Enterprise only. | positive | [browserbase.com](https://docs.browserbase.com/account/enterprise/zero-data-retention) | Exa, Parallel |
| Diffbot | HIPAA BAA | No HIPAA or BAA on Diffbot's privacy, GDPR, pricing, terms, or homepage; web search found none. | absence | [diffbot.com](https://www.diffbot.com/company/privacy) | Exa, Parallel, Firecrawl |
| Diffbot | Official MCP server | Official diffbot org repo, active in Aug 2026; README gives Diffbot's hosted remote server at mcp.diffbot.com/mcp. | positive | [github.com](https://github.com/diffbot/diffbot-mcp) | Exa, Parallel |
| Diffbot | SOC 2 | No SOC 2 claim on Diffbot's privacy, GDPR, pricing, terms, or homepage; web search found none. | absence | [diffbot.com](https://www.diffbot.com/company/privacy) | Exa, Parallel, Firecrawl |
| Diffbot | Zero data retention | README says the serverless API 'follows a Zero Data Retention policy' (note added Feb 2025). Repo isn't archived; llm.diffbot.com responds. | positive | [github.com](https://github.com/diffbot/diffbot-llm-inference) | Exa |
| Apify | HIPAA BAA | Compliance lists only GDPR and SOC 2; no HIPAA or BAA in resources or FAQ; web search found none. | absence | [apify.com](https://trust.apify.com/) | Parallel, Firecrawl |
| Apify | SOC 2 | Docs say the platform is SOC 2 Type II compliant (security, availability, confidentiality); no date on this page. | positive | [apify.com](https://docs.apify.com/security) | Exa, Parallel, Firecrawl |
| Apify | Zero data retention | Trust Center overview, resources, and FAQ list no ZDR offer; web search found none. | absence | [apify.com](https://trust.apify.com/) | Parallel, Firecrawl |
<!-- tables:findings_s6_new:end -->

### S5 entities verified and new to the KB (45, fact-level rule)
<!-- tables:findings_s5:start -->
| List | Entity | Domain | Found by | What the judge verified (paraphrase) |
|---|---|---|---|---|
| S5a | Chaos Gears | chaosgears.com | Exa | AWS Premier Tier services partner (homepage). Its Data & AI page lists an 'Agentic AI services' offering. The blog (4 Sep 2025, by a Chaos Gears AI engineer) is a hands-on guide to adding Tavily… |
| S5b | Grok (SpaceXAI) | x.ai | Parallel | Judged known; new under the fact-level rule (Amendment 7): Grok Business and Enterprise: the xAI leaf covers Grok's API web search, not the workplace product that searches Google Drive and the web. |
| S5b | Sana Agents | sanalabs.com | Exa, Firecrawl | Sana Agents Search covers documents, emails, and web pages, with a toggle to turn web search off. No web search engine is named. The subprocessor list (updated 9 Sep 2026) lists Anthropic,… |
| S5b | Realm Assistant | withrealm.com | Exa | Realm lets users choose Enterprise Search (connected apps such as Salesforce, Gong, and Google Drive), Web Search, or both. Neither the web search page nor the security page names a provider. |
| S5b | Genspark AI Workspace | genspark.ai | Parallel | Genspark for Business, an AI workspace for teams, advertises research that draws on the live web and internal knowledge, plus 200+ connectors (Salesforce, Google Workspace, Microsoft 365, Slack).… |
| S5b | Kore AI for Work | kore.ai | Exa | AI for Work connects enterprise systems (SharePoint, Salesforce, ServiceNow) and includes a Search Agent. A prebuilt Web Search tool is available for supported models. The capability matrix maps… |
| S5b | Generate Enterprise | iterate.ai | Exa | The cited help URL (help.generateapps.iterate.ai) now returns 404. The current docs confirm a web search toggle in Chat (only the typed query is sent), a knowledge base, and 60+ connectors. No… |
| S5b | Snowflake Cortex Agents | snowflake.com | Parallel | Judged known; new under the fact-level rule (Amendment 7): Snowflake Cortex Agents and Snowflake Intelligence: the leaves record Cortex Agents only as a channel for Exa's own integration; that Snowflake's… |
| S5b | Guru | getguru.com | Exa, Parallel, Firecrawl | Knowledge Agents can add web search, either the whole web or trusted domains, as a connected source, and answers blend web and internal sources. Web search runs as real-time federated queries. No… |
| S5b | Ejento AI | ejento.ai | Firecrawl | The homepage lists enterprise search across company knowledge (SharePoint, a knowledge corpus) and a real-time Web Search tool among the assistant's tools. No provider is named. |
| S5b | CustomGPT.ai | customgpt.ai | Exa | The page advertises Enterprise Search across SharePoint, Confluence, Notion, and Slack, and Live Web Search that adds real-time web results to the knowledge base. No provider is named. |
| S5b | Wissly | wissly.ai | Exa | Local-AI search over documents, ERP, and groupware. The cited /en/service page now redirects to /en/product, which doesn't mention web search. Wissly's docs do: answers come from internal… |
| S5b | Aisera Platform | aisera.com | Exa | Judged known; new under the fact-level rule (Amendment 7): Aisera: the only KB mention is a _scratch/ partner list; no leaf records its Public Search on Google. |
| S5b | Moveworks AI Assistant | moveworks.com | Exa, Parallel, Firecrawl | Enterprise Search covers internal sources, and the World Knowledge App and Quick GPT add live web search (on by default). The 1 Jul 2025 blog says an OpenAI model powers Quick GPT's web search,… |
| S5b | Omnifact | omnifact.ai | Firecrawl | Spaces hold knowledge bases synced from Google Drive, OneDrive, and SharePoint, and the agent also searches the web. The DPA lists Brave Software, Inc. as the sub-processor for web search through… |
| S5b | DocsBot Internal Knowledge Chatbot | docsbot.ai | Exa | An internal chatbot for employees over docs, wikis, and tools. Deep Research combines the knowledge base with live web search. No provider is named on the pages I checked (the Deep Research doc… |
| S5b | soev.ai | soev.ai | Exa | A Dutch sovereign-AI product that searches internal documents and the live web at the same time and merges both into one answer. It says queries aren't exposed to external search engines, but it… |
| S5c | Elnora AI | elnora.ai | Firecrawl | Elnora AI DPA subprocessor table lists Exa (Search API, semantic search and retrieval, US). Only date on page: Effective June 21, 2026. Not in KB. |
| S5c | Workshop | useworkshop.com | Firecrawl | Help-center article on Workshop's docs subdomain (curl blocked with 403; read via browser render and Zendesk's public article JSON) lists Exa for Web Search & Retrieval. page_date not claimed… |
| S5c | Dweet | dweet.com | Exa, Firecrawl | DWEET LTD's Nova sub-processor page lists Exa Labs Inc. for web search for company and market research. Both exa_role values fit. page_date per value: 2026-09-23 correct (page last updated… |
| S5c | Atlas Analytics | atlas-metis.com | Exa | ATLAS METIS (legal entity Atlas Analytics Pte. Ltd.) GDPR page subprocessor table lists Exa Labs, Inc. for web research and search API (USA). Page last updated August 2026. Not in KB. |
| S5c | Novita Inc. | novita.ai | Parallel | Novita docs describe the endpoint as 'a passthrough to Exa's Search API' through the Novita gateway, authenticated with the user's Novita API key, not an Exa key. So Novita resells Exa rather than… Known under the entity-level rule: Novita is in the KB as another customer's subprocessor… |
| S5c | OpenWhispr | — | Exa | OpenWhispr DPA subprocessor table lists Exa Labs, Inc. for web search for agent features (search query text, US). page_date not claimed (null), but page shows effective July 17, 2026, and version… |
| S5c | Libra AI | trylibra.ai | Exa | Libra AI DPA subprocessor table lists Exa (Exa AI, Inc., US) for semantic web search with embeddings. Page last updated March 2026. Not in KB. |
| S5c | Mainder | mainder.ai | Exa | Mainder Subprocessor Register (love.mainder.ai subdomain) lists Exa (Exa Labs, Inc.) for semantic web search (USA, SCC plus UK Addendum). page_date not claimed (null), but page shows Last updated… |
| S5c | Marcus (Zavo LTD) | heymarcus.ai | Firecrawl | Sub-processor page of Marcus (Zavo LTD trading as Marcus) lists Exa for web search. Page last updated 14 July 2026. Not in KB. |
| S5c | Jan | — | Exa | Jan desktop docs (Jan is built by Menlo Research): built-in Web Search is on by default, with Exa as the default provider on a free keyless endpoint; Tavily, SearXNG, and You.com are alternatives,… Known under the entity-level rule: Jan is in the KB as a Serper integration (competitive/serper.md:73). |
| S5c | Coherence (Brightyard) | getcoherence.io | Exa, Firecrawl | Coherence (Brightyard, Inc.) sub-processor page lists Exa AI, Inc. for web research for AI agents and lead discovery (US). Page last updated September 8, 2026. Same company and page as S5c-e26.… |
| S5c | Kolo | kolo.ai | Exa, Firecrawl | Client-side page, read via browser render. Kolo AI, Inc. lists Exa Labs, Inc. for web search and document retrieval API (US) under Search and Web Data, with Perplexity, Brave, and Firecrawl. Page… |
| S5c | Opally | opally.com | Exa | Opally ApS (Copenhagen; opally.dk is the Danish site of opally.com) public DPA PDF, Appendix B authorized sub-processors: Exa Labs, Inc. for AI-powered search and data processing API, also listed… |
| S5c | Kapso | kapso.com | Exa, Firecrawl | Needs an HTML Accept header (406 otherwise). Kapso subprocessor list names Exa Labs, Inc. for web search and research for AI and agent features. Page shows Last updated August 25, 2026 (also… |
| S5c | ILLIXIS | — | Exa | ILLIXIS sub-processor list names Exa Labs, Inc. for authoritative source discovery for research workflows (US). Page last updated September 24, 2026; claimed 2026-08-24 is not on the page (no… |
| S5c | Gamma | gamma.app | Firecrawl | curl blocked (403); read via browser render. Gamma Tech's list names Exa.ai for search functionality (US); Perplexity is also listed, as AI services. No date on page, so null is correct. KB… Known under the entity-level rule: Gamma is in the KB as a Firecrawl logo (competitive/firecrawl.md:59). |
| S5c | OrgOrg | orgorg.us | Exa | OrgOrg trust subprocessor list names Exa AI, Inc. for AI-powered search (USA). page_date not claimed (null), but page shows Last Modified July 21, 2026. Not in KB. |
| S5c | Levenza | levenza.com | Firecrawl | Levenza third-party subprocessor list, item 26: Exa Labs for web search. Page current as of July 8, 2026. Not in KB. |
| S5c | Elixiary AI | elixiary.com | Firecrawl | Elixiary AI list names Exa Labs, Inc. as a secondary web-search API that complements Tavily for its Marlow assistant. Page last updated September 10, 2026; claimed July 3, 2026 is not on the page… |
| S5c | Apollo.io | apollo.io | Parallel | Apollo.io AI Policy lists Exa among its current third-party AI providers that process Customer Data. Exa provides web search, used for AI research and prospecting. page_date not claimed (null),… Known under the entity-level rule: Apollo.io is in the KB as a competitor (competitive/b2b-data-providers.md). |
| S5c | Graphed | graphed.com | Firecrawl | Graphed subprocessor list names Exa Labs, Inc. (USA) for web search. Page last updated August 25, 2026. Not in KB. |
| S5c | MightyBot | mightybot.ai | Firecrawl | MightyBot subprocessor list names Exa Labs, Inc. for web search and research (US). Page last updated August 16, 2026. Not in KB. |
| S5c | Watchdog | — | Exa | Watchdog (watchdog.no) subprocessor list names Exa (Exa Labs Inc.) for web search during AI analysis, with search queries only, processed in the US. Page last updated September 2026. Not in KB. |
| S5c | Tracelight | tracelight.ai | Exa | Tracelight DPA Annex 2 subprocessor list names Exa Labs, Inc. for AI search engine processing. The page shows no date (the effective date is set per agreement), so null is correct. Not in KB. |
| S5c | Revelica | revelica.com | Firecrawl | Revelica sub-processors, web search and retrieval section: Exa for web search and content retrieval used by the product agent in research playbooks. Page last updated May 29, 2026. Not in KB. |
| S5c | Scripe | scripe.io | Exa | Scripe GmbH sub-processor list names Exa Labs, Inc. (USA) for AI-supported web research (search queries). Page last updated September 2026; claimed 2026-08 is not on the page (no archive copy).… |
| S5c | RingCentral | ringcentral.com | Firecrawl | RingCentral subprocessor list, AI Representative (AIR Pro) tab: Exa Labs LLC, search engine API, US, at 430 Shotwell St, San Francisco (the address other lists give for Exa Labs Inc.). List… |
| S5c | CounselAI | — | Exa | CounselAI subprocessor list, Search Services section, names Exa (exa.ai) for semantic web search (USA); Brave Search is also listed. Page last updated February 16, 2026. Not in KB. |
<!-- tables:findings_s5:end -->

### SP vendors new to the KB (11)
<!-- tables:findings_sp:start -->
| Domain | What it sells (judge's summary) | Judge's note |
|---|---|---|
| anysite.io | Web-data API/MCP for agents: 3,500+ endpoints across 650+ sources, incl. B2B leads | Example URL (its Brave search endpoint docs) redirected to app docs; homepage confirms paid MCP/API plans. No 'anysite' match in KB. |
| serpapi.org | SerpScraper.dev: Google SERP APIs (web, AI Overview, news, maps, scholar) plus utility APIs | SerpApi lookalike domain; both URLs redirect to serpscraper.dev, a separate SERP vendor. No KB match for serpscraper or serpapi.org. |
| serpentapi.com | Beta SERP API across eight engines incl. Google, Bing, Baidu, Yandex, Naver | KB's only 'Serpent API' mention is apiserpent.com, cited once in competitive/serper.md. That is a different operator; serpentapi.com disclaims lookalikes. |
| seoserpapi.com | SEO-oriented SERP API (top-100 results), backlink API and search-volume data | Small SEO tool (links to Czech SEO sites). KB 'SEO SERP API' grep hits are only 'DataForSEO SERP API'. |
| serpsearch.com | Google search, maps, reviews, images and news scraping API; top-up credits | No KB match for serpsearch or 'SERP Search'. |
| serpingapi.com | Serper-compatible real-time Google SERP JSON API; 1,000 free searches | No KB match for serping. |
| serpstack.com | APILayer's real-time Google SERP JSON API with proxy and CAPTCHA handling | No KB match for serpstack or APILayer. |
| scraperapi.com | Web scraping API (proxies, JS rendering) plus structured Google SERP endpoints | Example URL blocked (403 curl, 522 WebFetch); product confirmed via scraperapi.com search results. KB 'scraper API' hits are generic or Oxylabs/Bright Data. |
| serpermatrix.com | Google search and page-scrape API for AI agents (web, images, scholar, places) | Low credibility: homepage reuses Firecrawl's template and section labels, 'Backed by BinoMatrix', unverified logo wall. Has its own docs subdomain. No KB match. |
| reserp.ai | Google Search API (web, images, shopping, news, videos, places) from $0.20/1K | No KB match for reserp. |
| serpbase.dev | Credit-based Google search JSON API (web, maps places, images, news) | No KB match for serpbase. |
<!-- tables:findings_sp:end -->

## See Also
- [Experiment overview](overview.md)
- [Stage A results](results-stage-a.md)
- [Pre-registration and amendments](preregistration.md)
- [Stage B judge brief](judge-brief-b.md)
- Parallel FindAll (KB page `competitive/parallel-findall`, not published)
- Firecrawl (KB page `competitive/firecrawl`, not published)
- Exa Agent (KB page `features/exa-agent`, not published)
- Websets (KB page `features/websets`, not published)
