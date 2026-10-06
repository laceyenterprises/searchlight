---
title: "Search-provider experiment: Stage A results"
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
token_estimate: 7799
confidence_score: 0.70
provenance_reviewed: false
tags: ["experiments", "search-provider-eval", "results", "exa", "tavily", "parallel", "firecrawl"]
provenance_summary: >-
  Stage A of the pre-registered search-provider experiment, run 2026-09-26: cheap retrieval probes (S1–S4) on Exa,
  Tavily, Parallel, and Firecrawl. S2 and S3 are scored by script, with post-hoc diagnostics. S1 and S4 are graded by
  six provider-blind judge agents working on shuffled pools. The judges verified every counted claim on its page, and
  credit goes per claim (Amendment 4). The first grading pass (v1) is kept for comparison. Every table is generated
  from the data by `code/make_tables_a.py`. Internal only, small n.
---
> **Record note (Searchlight, 2026-10-06).** This file is the experiment's own record, copied from the private
> knowledge base where the experiment ran. Links between record files are converted to relative links; links to
> other knowledge-base pages, which are not published, are shown as plain text with the page's path. Nothing else is
> changed unless a further note says so. The Searchlight summary is [REPORT.md](../REPORT.md).

> [!summary] Quick Peek
> - Purpose: What each search API added to this KB in Stage A, in hard numbers:
>   - S1, known unknowns;
>   - S2, recall of the sources the KB already cites;
>   - S3, pages native tools couldn't read;
>   - S4, two-week freshness.
> - Headline (v2 re-grade, strict per-claim credit):
>   - **Exa led S1 and S4.** On known unknowns, it was credited with 25 new or correcting claims, against Parallel 19, Tavily 11, and Firecrawl 10. 11 of those claims no other provider supported, against Parallel 4, Tavily 2, and Firecrawl 0. On two-week news it found 20 new facts, against Tavily 12, Parallel 3, and Firecrawl 2.
>   - **Firecrawl and Parallel led on pages native tools couldn't read**, recovering 14 and 12 of 15.
>   - **Recall of cited sources was close.** With identical inputs, the four providers found 18, 16, 19, and 17 of 20.
>   - **Across providers**, 18 of 40 known unknowns got a verified new or correcting claim.
> - Read this when: You're choosing a provider for a research task on this KB, or reading Stage B.
> - Skip this when: You're writing external copy. The results are small-n, from one run date, and the owner decided they won't be published.
> - Key links: [Experiment overview](overview.md); [Pre-registration (Amendment 4)](preregistration.md); [Design](design.md)
> - Confidence: 0.70. Pre-registered sets, a blind re-grade on shuffled pools, and page-verified claims. Lowered for small samples, a single run, judges that are Claude agents, and analysis rules that were fixed only after review (Amendment 4).

> [!warning] Internal only
> Stage A was 85 question-level calls per provider on this KB's own research backlog, all on 2026-09-26. It isn't a benchmark. The owner is a customer evaluating providers for its own use and decided the results won't be published. Parallel's and Tavily's terms restrict benchmark and competitive use (see the [README](overview.md)).

# Stage A results

## At a glance
> [!cite] Section provenance
> - Source basis: generated from `data/stage-a/` by `code/make_tables_a.py`. The strict per-claim credit is from the v2 re-grade (Amendment 4).
> - Confidence: 0.70

<!-- tables:glance:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| S1 new or correcting claims, strict (known unknowns, 40 questions) | 25 | 11 | 19 | 10 |
| … no other provider supported | 11 | 2 | 4 | 0 |
| S4 new dated facts, strict (10 companies, two weeks) | 20 | 12 | 3 | 2 |
| S3 pages recovered that native tools couldn't read (of 15) | 5 | 10 | 12 | 14 |
| S2 cited sources found in the top 10, identical inputs (of 20) | 18 | 16 | 19 | 17 |
| Stage A run spend | $0.50 | 146 credits ($1.17) | $0.37 | 402 credits ($0.30–$2.01) |
| Median latency, ms | 1,230 | 2,995 | 2,343 | 1,171 |
<!-- tables:glance:end -->

## Setup
> [!cite] Section provenance
> - Source basis: `code/sets/S1–S4.json`, `code/harness.py`, and the pre-registration with Amendments 1–4 [observed — 2026-09-26].
> - Confidence: 0.85

**Providers and parameters.** Each provider ran with the best-practice settings from its own docs, and all four ran on the same day:
- **Exa:** `/search` auto with highlights, and `/contents` with `maxAgeHours: 0` (a forced live crawl).
- **Tavily:** search at `advanced` depth with chunks, and `extract` at `advanced`.
- **Parallel:** `/v1/search` advanced mode, with an objective plus hand-written keyword queries, and `/v1/extract` with full content.
- **Firecrawl:** `/v2/search` with scrapes of the top 3 results (social sites excluded), and `/v2/scrape` with auto proxy.

**Baseline.** The baseline is the KB itself, built with native tools. For v2, that's the base branch at 933777c, which includes PRs #23 and #24, with `experiments/` excluded.

**Inputs weren't identical.** Exa and Tavily got the question text. Parallel also got up to three hand-written queries, and Firecrawl got a hand-written keyword query. Some S2 queries leaked answer terms, which D2 corrects (below).

**Sets:**

| Set | Items |
|---|---|
| S1 | 40 known unknowns |
| S2 | 20 known facts |
| S3 | 15 pages native tools couldn't read |
| S4 | 10 companies, with news from 2026-09-12 to 2026-09-26 |

**Amendments:**
- **1–3**, during the run: two request-format fixes (Parallel search, Parallel extract) and two Firecrawl cost guards (a 3-page PDF cap and a social-site skip list). No question or metric changed.
- **4**, after the PR #25 review: the re-grading rules, plus the post-hoc diagnostics D1–D3.

## Cost and latency
> [!cite] Section provenance
> - Source basis: `data/stage-a/runs/ledger.json` and the run records [observed — 2026-09-26]. Parallel's dollars are list-price estimates from the harness config, not billed amounts. Firecrawl's dollar range runs from its Scale plan to Hobby top-ups.
> - Confidence: 0.85

<!-- tables:cost:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Question-level records, S1–S4 | 85 | 85 | 85 | 85 |
| API calls billed, all Stage A work (incl. scrapes and diagnostics) | 110 | 105 | 123 | 315 |
| Spend, Stage A runs | $0.50 | 146 credits ($1.17) | $0.37 | 402 credits ($0.30–$2.01) |
| Spend, post-hoc diagnostics D1–D3 | $0.14 | 40 credits ($0.32) | $0.10 | 92 credits ($0.07–$0.46) |
<!-- tables:cost:end -->

- **Spend.** Stage A stayed inside every provider's free tier.
- **Grading cost.** Claude-side grading used about 1.26M tokens for v1 (three judges) and about 2.96M for the v2 re-grade (six judges). These figures come from the agents' usage reports.
- **Latency.** Measured by this harness, from one machine. It isn't a latency benchmark:

<!-- tables:latency:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Median latency, ms (successful calls, S1–S4) | 1,230 | 2,995 | 2,343 | 1,171 |
| 90th percentile, ms | 3,050 | 4,624 | 3,610 | 1,569 |
| Calls measured | 85 | 85 | 85 | 85 |
<!-- tables:latency:end -->

## S2: recall of the primary source the KB already cites (top 10)
> [!cite] Section provenance
> - Source basis: `grading/summary_stageA_scripted.json` and the D2 re-run records [observed — 2026-09-26].
> - Confidence: 0.80

<!-- tables:s2:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Strict, pre-registered inputs (exact cited URL, prefix match) | 18/20 | 13/20 | 20/20 | 18/20 |
| Domain-level, pre-registered inputs (post hoc) | 20/20 | 18/20 | 20/20 | 19/20 |
| Strict, identical inputs (D2, post hoc) | 18/20 | 16/20 | 19/20 | 17/20 |
| Domain-level, identical inputs (D2) | 20/20 | 19/20 | 20/20 | 19/20 |
<!-- tables:s2:end -->

- **How it's counted.** "Strict" is a prefix match on the exact URL the KB cites. "Domain-level" counts any result on that source's registrable domain; it's post hoc, added after spot checks found same-fact pages elsewhere on the source's site.
- **Reading** [inferred]:
  - With identical inputs, Parallel's edge over Exa shrinks from 2 items to 1.
  - Tavily moved from 13 to 16 on the same question text between two runs the same day.
  - S2 differences of 1–3 items are within run-to-run noise. Treat S2 as calibration (every provider finds the sources the KB cites), not a ranking.

## S3: pages native tools couldn't read
> [!cite] Section provenance
> - Source basis: S3 run records, their `detail` status fields, and the D1 re-run [observed — 2026-09-26].
> - Confidence: 0.75

<!-- tables:s3:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Scripted: ≥500 characters and a target keyword | 6/15 | 11/15 | 13/15 | 15/15 |
| After the b04 correction (CodeRabbit redirect), reported | 5/15 | 10/15 | 12/15 | 14/15 |
<!-- tables:s3:end -->

- **The test is weak.** A page counts as recovered with at least 500 characters and one target keyword. The keyword is sometimes generic, such as "review", "Exa", or "Websets".
- **Low-text recoveries.** <!-- tables:s3_low:start -->
2 of the counted recoveries rest on fewer than 2,000 characters: Exa b06 (598 chars; matched review); Parallel b06 (1,453 chars; matched review).
<!-- tables:s3_low:end -->
- **Hand correction.** CodeRabbit's trust-center URL (b04) now redirects to its homepage for every provider, so it counts as not recovered.
- **Exa's misses.** <!-- tables:s3_exa:start -->
Exa's 10 misses (after the b04 correction): 5 were crawl errors in its own status field under the forced live crawl (`maxAgeHours: 0`): b05, b10, b11, b12, b15. With default cache settings (D1, post hoc), 4 of those 5 came back readable (b10, b11, b12, b15), which would put Exa at 9/15.
<!-- tables:s3_exa:end -->
- **Reading** [inferred]:
  - Most of Exa's S3 deficit came from the forced live crawl (`maxAgeHours: 0`), not from Exa being unable to reach the pages. Its cache held readable copies of four of them.
  - JavaScript-rendered trust centers remain a real gap for Exa, which returned only script shells for the Vanta pages.
  - Firecrawl's and Parallel's lead on rendered pages holds: only they recovered G2's Exa reviews, and only Firecrawl returned Apollo's plan names.

## S4: new, dated intel in a two-week window (v2)
> [!cite] Section provenance
> - Source basis: two blind judges on shuffled pools. Each fact was verified on its page and credited to the providers whose own result page states it (`grading/v2/`) [observed — 2026-09-26].
> - Confidence: 0.72

The judges split each event into facts, blind, before unblinding. A fact counts if it's dated inside the window, verified, has usefulness 2 or more, and is either a net-new event or a new detail on an event the KB already has.

**Strict credit:**

<!-- tables:s4_strict:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Verified new facts (usefulness ≥ 2) | 20 | 12 | 3 | 2 |
| … net-new events | 6 | 5 | 1 | 1 |
| … new details on known events | 14 | 7 | 2 | 1 |
| Facts no other provider supported | 11 | 4 | 0 | 1 |
| Events with at least one new fact | 8 | 7 | 2 | 2 |
| Events unique to the provider | 3 | 2 | 0 | 0 |
<!-- tables:s4_strict:end -->

**Broad credit** (partial support also counts):

<!-- tables:s4_broad:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Verified new facts (usefulness ≥ 2) | 20 | 12 | 4 | 4 |
| … net-new events | 6 | 5 | 1 | 1 |
| … new details on known events | 14 | 7 | 3 | 3 |
| Facts no other provider supported | 11 | 3 | 0 | 1 |
| Events with at least one new fact | 8 | 7 | 3 | 3 |
| Events unique to the provider | 3 | 1 | 0 | 0 |
<!-- tables:s4_broad:end -->

**Source types.** 13 of the 25 new facts are on primary pages (the company's own page or a filing) and 12 on secondary pages.

**Superseded first pass (v1).** Facts were split by hand after unblinding, and Exa's results always sat in the first ten slots:

<!-- tables:s4_v1:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| v1: new facts (hand-split after unblinding) | 9 | 5 | 0 | 0 |
| v1: unique | 6 | 2 | 0 | 0 |
<!-- tables:s4_v1:end -->

**Reading** [inferred]:
- The v2 re-grade, with shuffled slots, facts split blind, and the current baseline, didn't erase Exa's S4 lead. It strengthened it, because v2 judges broke events into more facts.
- Tavily is a clear second on freshness.
- **Firecrawl's date filter may not work.** Its `tbs` date filter returned only undated evergreen pages, even in a direct test (D3: 6 results with the filter against 10 without, and all undated). Its S4 near-zero reflects that more than a lack of coverage.

## S1: known unknowns our native research couldn't answer (v2)
> [!cite] Section provenance
> - Source basis: four blind judges on shuffled pools. Each answer was split into claims, each claim was verified on its page and credited per claim (`grading/v2/`) [observed — 2026-09-26].
> - Confidence: 0.72

<!-- tables:s1_any:start -->
18 of 40 questions got a verified new or correcting claim that some provider's own result page states (strict); 19 counting partial support.
<!-- tables:s1_any:end -->

**Strict credit** (the provider returned a page that states the claim):

<!-- tables:s1_strict:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Questions with a verified new or correcting claim (of 40) | 17 | 8 | 11 | 7 |
| … of them in q01–q20 | 11 | 7 | 7 | 5 |
| New or correcting claims credited | 25 | 11 | 19 | 10 |
| … of which correct a KB claim | 6 | 3 | 7 | 5 |
| Claims no other provider supported | 11 | 2 | 4 | 0 |
| Questions the provider's own results fully resolve | 9 | 4 | 2 | 1 |
| Usefulness-weighted score | 85 | 44 | 67 | 43 |
<!-- tables:s1_strict:end -->

**Broad credit** (partial support also counts):

<!-- tables:s1_broad:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| Questions with a verified new or correcting claim (of 40) | 18 | 11 | 13 | 9 |
| … of them in q01–q20 | 12 | 9 | 9 | 6 |
| New or correcting claims credited | 30 | 18 | 22 | 14 |
| … of which correct a KB claim | 6 | 3 | 7 | 5 |
| Claims no other provider supported | 11 | 0 | 2 | 0 |
| Questions the provider's own results fully resolve | 12 | 6 | 3 | 4 |
| Usefulness-weighted score | 98 | 64 | 78 | 56 |
<!-- tables:s1_broad:end -->

**Stop rule.** <!-- tables:stop_rule:start -->
Re-checked under v2 for q01–q20: the most questions any one provider fully resolved alone was 4 (strict) and 5 (broad); the most with a new claim was 11 (strict) and 12 (broad).
<!-- tables:stop_rule:end -->
The rule needed at least 3 questions from one provider, so continuing to q21–q40 was right under both readings.

**Robustness against v1.** <!-- tables:v1_v2_agreement:start -->
v1 found a new or correcting answer on 16 questions; v2 (strict) on 18. They agree on 15. Only in v1: q26. Only in v2: q05, q15, q35.
<!-- tables:v1_v2_agreement:end -->
- **q26, v1 only.** The v2 judge treated Groq Compound on Tavily as already in the KB, which records "reported Tavily".
- **q05, q15, q35, v2 only.** OpenAI's HIPAA page confirming its own index; Google's amended complaint against SerpApi; Perplexity's SDK search modes.

**Superseded first pass (v1),** with per-question credit:

<!-- tables:s1_v1:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| v1: questions with a verified new or correcting answer (per-question credit) | 15 | 11 | 12 | 10 |
| v1: usefulness-weighted | 38 | 28 | 32 | 25 |
<!-- tables:s1_v1:end -->

## Overlap and cost per new item
> [!cite] Section provenance
> - Source basis: `grading/v2/scored_v2.json` and the ledger [observed — 2026-09-26].
> - Confidence: 0.75

<!-- tables:overlap:start -->
New S1 claims by number of providers supporting them (strict): 0: 3, 1: 17, 2: 7, 3: 6, 4: 4. S4 new facts: 1: 16, 2: 7, 3: 1, 4: 1.
<!-- tables:overlap:end -->

**Pairs sharing new S1 claims:**

| Pair | Shared claims |
|---|---|
| Exa + Parallel | 12 |
| Exa + Tavily | 9 |
| Parallel + Firecrawl | 8 |
| Exa + Firecrawl | 7 |
| Parallel + Tavily | 7 |
| Tavily + Firecrawl | 6 |

In S4, Exa and Tavily shared 8 facts; every other pair shared 3 or fewer.

<!-- tables:cost_per_item:start -->
| | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| New S1 claims plus S4 facts, strict credit | 45 | 23 | 22 | 12 |
| Stage A run spend per new item | $0.011 | $0.051 | $0.017 | $0.025–$0.168 |
<!-- tables:cost_per_item:end -->

## What Stage A says [inferred]
1. **Native research had run dry, and the search APIs still added verified facts.** Under strict credit, 18 of 40 known unknowns got a verified new or correcting claim, and the two-week window produced 25 new dated facts. The winning pages were long-tail primary sources: subprocessor lists, court dockets, AWS reference pages, SDK source, and trade press.
2. **Exa contributed the most unique material, per the numbers above.** It led on unique claims in S1 (11), on questions its own results fully resolved (9), and on new S4 facts (20), at the lowest Stage A cost per new item ($0.011).
3. **Parallel was a strong second on known unknowns.** It had 19 claims, including 7 corrections, the most of any provider.
4. **Firecrawl and Parallel lead on pages that need rendering.** Exa's gap there is partly a configuration effect (D1).
5. **For this KB's research workflow**, send discovery and freshness sweeps to Exa, and send pages that need rendering to Firecrawl or Parallel. One provider for everything leaves gaps.

## Caveats
- **Sample.** One run date, small samples, one workload (this KB's backlog), and one judge per chunk. The judges are Claude agents.
- **Inputs.** Parallel's hand-written queries and Firecrawl's keyword queries differ from the question text, and some S2 inputs leaked answers. D2 shows the S2 effect is small. S1 and S4 weren't re-run with identical inputs.
- **Firecrawl content.** Its content comes from scraping only its top 3 results, a cost choice made before the run. The other providers return excerpts for all 10.
- **Credit.** A provider gets credit when it returned the page. It doesn't say who would have found a fact if the others hadn't existed.
- **Baseline and budget.** v1 used a baseline from before PR #24. v2 uses the base branch at 933777c. The native baseline had no fixed call budget; the providers did.
- **Record gaps.** Firecrawl's ledger shows 34 credits and 5 calls from before S1 that no run record covers, most likely the smoke test from before the Amendment 1 fixes. The logs cover 336 of 355 records.

## Stage B gate
> [!cite] Section provenance
> - Source basis: this page's S3 and S4 tables, and the design's gate rule.
> - Confidence: 0.75

**The design's gate.** Signal on a provider-and-workload basis: at least 5 verified net-new facts, or at least 30% recovery of pages native tools couldn't read.

**How it was applied.** No Stage A set tests the S5 or S6 workloads, so the gate was applied per provider on any Stage A signal. This is an interpretation, logged in Amendment 4.
- **Exa:** 20 new S4 facts (6 of them net-new events), and 5 of 15 on S3 (33%).
- **Tavily:** 12 S4 facts, and 10 of 15 on S3.
- **Parallel:** 12 of 15 on S3 (80%).
- **Firecrawl:** 14 of 15 on S3 (93%).

Stage B (entity lists, schema fill, find-similar, Monitors) runs only on providers that sell those capabilities. The owner approved it on 2026-09-26 with a $40 total cap.

## Appendix: verified findings (v2, for the KB-facts PR)
> [!cite] Section provenance
> - Source basis: the v2 judge files. Claim and fact text is the judge's paraphrase, and the "Verified on" page is where the judge confirmed it. X, LinkedIn, and similar social posts aren't linked here (they're in the local grading data). Findings go to canonical leaves in a separate PR, cited to the primary page.
> - Confidence: 0.75

**S1: new or correcting claims (usefulness 2 or more):**

<!-- tables:findings_s1:start -->
| Q | Claim (judge's paraphrase) | Novelty | Use | Verified on | Credited (strict) |
|---|---|---|---|---|---|
| q01 | Anthropic's help center says Claude for Government's Web Search MCP connector calls the Brave Search API, sends only the query string, and that Brave doesn't persistently store queries. | new | 2 | [claude.com](https://support.claude.com/en/articles/14503775-mcp-web-search) | Exa |
| q01 | Anthropic's Claude Desktop third-party docs say Web Search runs at the inference provider (the provider's own backend on Google Cloud's Agent Platform and Microsoft Foundry; not native on Amazon Bedrock), and a… | new | 2 | [claude.com](https://claude.com/docs/third-party/claude-desktop/web-tools) | Parallel |
| q03 | Tavily's standard terms bar customers from submitting HIPAA-protected health information: Platform Terms section 8.2, with the DPA and the AUP also barring it absent Tavily's written consent. | correction | 3 | [tavily.com](https://www.tavily.com/terms) | Exa, Firecrawl, Parallel, Tavily |
| q03 | Tavily's DPA (section 1.8 of the MSA/DPA/SLA) and its AUP allow HIPAA PHI only with Tavily's prior written consent; neither references a BAA. | new | 2 | [tavily.com](https://www.tavily.com/legal/tavily-msa-dpa-sla.pdf) | Exa, Parallel, Tavily |
| q04 | Keyless caps are time-windowed: Tavily's JS SDK source says a keyless request can be rejected when an hourly, daily, or monthly cap is reached. | new | 2 | [github.com](https://github.com/tavily-ai/tavily-js/blob/main/src/errors.ts) | none (partial support only) |
| q05 | OpenAI's help center confirms an OpenAI-run search index: in HIPAA-eligible ChatGPT workspaces, web search and deep research use information in OpenAI's index. | new | 2 | [openai.com](https://help.openai.com/en/articles/20001069-hipaa-eligible-products-and-functionality) | Exa |
| q05 | OpenAI names Bing among ChatGPT's third-party search providers: the HIPAA page says those workspaces send no queries to third-party providers 'e.g., Bing', and the search help page points to Microsoft's privacy… | new | 2 | [openai.com](https://help.openai.com/en/articles/20001069-hipaa-eligible-products-and-functionality) | none (partial support only) |
| q05 | Independent 2026 analyses of ChatGPT's streamed search events found four result sources: labrador (OpenAI's own index), bright and oxylabs (scraping providers), and serp. | new | 2 | [rentierdigital.xyz](https://rentierdigital.xyz/blog/chatgpt-labrador-search-engine-index) | Exa |
| q07 | Mistral's Trust Center lists Brave Software, Inc. as its only Web Search subprocessor (United States), for Vibe and Studio/API. | new | 2 | [mistral.ai](https://trust.mistral.ai/subprocessors/) | none (partial support only) |
| q07 | Mistral's websearch docs show example web_search tool_reference results tagged with source 'brave'. | correction | 3 | [mistral.ai](https://docs.mistral.ai/studio/agents/agent-tools/websearch) | Exa, Firecrawl, Parallel, Tavily |
| q07 | Mistral's privacy policy keeps Agents API inputs and outputs until the user terminates the account; most other API data is kept 30 rolling days for abuse monitoring unless ZDR is on. | new | 2 | [mistral.ai](https://legal.mistral.ai/terms/privacy-policy) | Tavily |
| q07 | Mistral's zero data retention applies only to stateless endpoints and excludes Agents and Conversations. | new | 2 | [mistral.ai](https://docs.mistral.ai/admin/monitor-comply/zero-data-retention) | Parallel |
| q08 | AWS's HIPAA Eligible Services Reference (last updated 2026-09-03) lists Amazon Bedrock AgentCore with no feature exclusions and says all features of listed services are eligible unless specifically noted. | new | 2 | [amazon.com](https://aws.amazon.com/compliance/hipaa-eligible-services-reference/) | Exa |
| q11 | Valyu's only named investor is Andreessen Horowitz (a16z crypto), from March 2024. | new | 2 | [vcbacked.co](https://www.vcbacked.co/company/valyu) | Exa, Parallel, Tavily |
| q11 | Valyu Network was in a16z crypto's CSX Spring 2024 accelerator cohort (announced 2024-03-26). | new | 2 | [a16zcrypto.com](https://a16zcrypto.com/posts/article/crypto-startup-accelerator-csx-spring-2024-cohort/) | Exa |
| q13 | A Databricks blog post (2024-08-21) named Nimble among the new Databricks Marketplace data providers of Q2 2024, for real-time retail pricing and inventory data. | new | 2 | [databricks.com](https://www.databricks.com/blog/databricks-marketplace-welcomes-47-new-data-providers-q2-2024) | Tavily |
| q13 | Nimble describes Databricks as a product partner as well as an investor; in June 2025 it said it 'partnered with Databricks' to build an e-commerce/CPG agent. | new | 2 | [nimbleway.com](https://www.nimbleway.com/blog/ecommerce-cpg-agentic-ai-system-nimble-databricks) | Exa, Firecrawl, Tavily |
| q14 | Reddit, Inc. (plaintiff) filed the 2026-09-25 motion (Dkt 144), under Rule 12(b)(6), to dismiss SerpApi LLC's counterclaims. | new | 2 | [courtlistener.com](https://storage.courtlistener.com/recap/gov.uscourts.nysd.651592/gov.uscourts.nysd.651592.144.0.pdf) | Exa, Parallel |
| q14 | SerpApi's counterclaim (Dkt 119, filed 2026-08-28) pleads monopolization and attempted monopolization under Sherman Act s.2, with Clayton Act ss.4/16 remedies, plus four declaratory-judgment counts. | new | 2 | [courtlistener.com](https://storage.courtlistener.com/recap/gov.uscourts.nysd.651592/gov.uscourts.nysd.651592.119.0.pdf) | Exa, Parallel |
| q14 | The theory is that Reddit gave Google exclusive crawl access, under a partnership reportedly worth about $60M a year. From 2024-07-01 it served other crawlers (Bing, DuckDuckGo, Mojeek, Qwant) a blocking robots.txt.… | new | 2 | [courtlistener.com](https://storage.courtlistener.com/recap/gov.uscourts.nysd.651592/gov.uscourts.nysd.651592.119.0.pdf) | Exa, Parallel, Tavily |
| q14 | SerpApi seeks treble damages, an injunction against requester-specific robots.txt directives, and a declaration that Reddit has no cognizable DMCA claims before January 2025. | new | 2 | [courtlistener.com](https://storage.courtlistener.com/recap/gov.uscourts.nysd.651592/gov.uscourts.nysd.651592.119.0.pdf) | Exa, Parallel |
| q15 | Google's amended complaint (2026-08-10) narrows the case to licensed content. It pleads licensing agreements with Reddit and two unnamed licensors, and drops the Google Shopping and Maps examples. | new | 2 | [ppc.land](https://ppc.land/googles-serpapi-case-forces-its-licensing-deals-into-the-open/) | Exa |
| q17 | Legora's pre-approved sub-processor page (updated 2026-07-06) lists Exa Labs Inc. for web search in its United States list; its EU list uses Linkup for web search. | correction | 3 | [legora.com](https://legora.com/legal/eu-pre-approved-sub-processors) | Parallel |
| q17 | Zed's subprocessor list (updated 2026-03-02) names Exa Labs for AI-powered contextual search and retrieval behind Zed-hosted models. | correction | 3 | [zed.dev](https://zed.dev/subprocessors) | Firecrawl, Parallel |
| q17 | Exa Labs is on the subprocessor pages of more companies the KB doesn't track: DigitalOcean (optional, public preview, web-based search), Intapp (Intapp Celeste, web grounding), Day AI (web search and enrichment,… | new | 2 | [digitalocean.com](https://www.digitalocean.com/trust/subprocessors) | Exa, Firecrawl, Tavily |
| q20 | LinkedIn lists Exa in the 51-200 employee band, with 231 members listing Exa as their employer (September 2026). | new | 2 | linkedin.com post (URL in the local grading data) | Exa, Firecrawl, Parallel, Tavily |
| q22 | Firecrawl self-reports eight-figure ARR in its first year, more than doubled in its second, with no exact figure or period. Its Ashby job board carries the line (Sales Engineer post, datePosted 2026-08-04). | correction | 3 | [ashbyhq.com](https://jobs.ashbyhq.com/firecrawl/d35c74ec-bd98-485f-81e7-663cfe2d5dc3) | Exa |
| q28 | Legora's Pre-approved Sub-processors page (last updated 2026-07-06) lists Exa Labs Inc. for 'Web Search' with US data residency. Exa is not listed in the EU or Asia-Pacific sections. | correction | 3 | [legora.com](https://legora.com/legal/eu-pre-approved-sub-processors) | Exa |
| q29 | By 2026-07-30, during the July launch window, Firefox for iOS Quick Answers named Exa as its answer provider. MacObserver's hands-on (Firefox Beta via TestFlight) says every response footer identifies Exa, and a… | correction | 3 | [macobserver.com](https://www.macobserver.com/news/we-tried-firefoxs-new-ai-quick-answers-on-iphone-heres-how-it-works/) | Exa, Firecrawl, Parallel, Tavily |
| q29 | firefox-ios commits from 2026-07-08 and 2026-07-10 added a 'Powered by <Model>' footer and Nimbus model selection between 'exa' and 'liner' for Quick Answers, plus Exa-specific system-prompt instructions. | new | 2 | [github.com](https://github.com/mozilla-mobile/firefox-ios/commit/5f0ae2f82b45a85ce74be58ca6168ae472ff6e71) | Exa |
| q33 | Crustdata balances sit in two wallets. The recurring wallet holds the plan's per-cycle credit grant and is spent first. The top-up wallet holds purchased credits, which roll over month to month and expire 12 months… | new | 2 | [crustdata.com](https://docs.crustdata.com/general/credits) | Exa, Parallel |
| q33 | The reseller treg.to sells Crustdata people and company search at $0.009 per result and person enrich at $2.10 per result, and says it passes through the provider's own rate with no markup. | new | 2 | [treg.to](https://treg.to/tools/crustdata) | Exa |
| q35 | Perplexity's official Node SDK types the Search API request with search_mode 'web', 'academic' or 'sec' (SEC mode targets filings, not company entities), and its README shows an academic search through… | correction | 3 | [github.com](https://github.com/perplexityai/perplexity-node) | Parallel |
| q36 | JP Schmetz, Brave Search's chief of ads, told MediaPost (2026-04-06) that the API receives several billion queries a month but declined to share exact numbers. | new | 2 | [mediapost.com](https://www.mediapost.com/publications/article/414024/braves-new-world-less-data-more-agentic.html) | Exa |
| q37 | The $49/month Websets plan (8,000 credits a month) is named Starter and includes 100 results per Webset, 2 team seats and 10 enrichments per Webset. | correction | 3 | [archive.org](https://web.archive.org/web/20260509200752/https://websets.exa.ai/websets/billing) | Exa, Firecrawl, Parallel |
| q37 | Pro costs $449/month for 100,000 credits, with 1,000 results per Webset, 10 seats and 50 enrichments. Enterprise is custom, with 5,000 results and 100 enrichments per Webset and volume credit discounts. | new | 2 | [archive.org](https://web.archive.org/web/20260509200752/https://websets.exa.ai/websets/billing) | Firecrawl, Parallel |
| q37 | Websets credit costs: 10 per result matching all criteria, 0 for non-matches, 5 per email or phone number, 2 per row for other enrichments, 2 per resolved import result, 2 per API preview request and 5 per API recall… | new | 2 | [archive.org](https://web.archive.org/web/20260509200752/https://websets.exa.ai/websets/billing) | Firecrawl, Parallel |
<!-- tables:findings_s1:end -->

**S4: new facts (usefulness 2 or more):**

<!-- tables:findings_s4:start -->
| Co. | Fact (judge's paraphrase) | Novelty | Source type | Verified on | Credited (strict) |
|---|---|---|---|---|---|
| f01 | Exa co-founder Jeff Wang says Ultra uses heavy agent parallelization, code execution and a database tool, orchestrated by frontier models (X, 2026-09-25). | new detail | primary | x.com post (URL in the local grading data) | Exa |
| f01 | On AIMultiple's DR-20 Bench (20 business research briefs, blind-graded), Exa Agent scored 0.584, fourth of five, below Codex CLI 0.790, Claude Code 0.775 and Grok CLI 0.699; statistically tied… | net new | secondary | [aimultiple.com](https://aimultiple.com/ai-deep-research) | Exa, Tavily |
| f01 | Exa Agent cost $1.00 per task ($20 total API spend) vs $13.38-$18.35 for the two leaders, and fully reproduced 89 of 434 required facts vs 202 for Claude Code. | net new | secondary | [aimultiple.com](https://aimultiple.com/ai-deep-research) | Exa, Tavily |
| f03 | OpenAI says GPT-6 Astra let Parallel's agents finish research tasks in half the time at roughly half the cost of prior models (test: six labor-market statistics, four states, six months). | net new | secondary | [openai.com](https://openai.com/index/parallel-cuts-time-and-cost-with-astra) | Exa, Tavily |
| f03 | Lovable's changelog adds a Parallel connector: live web search, reading public pages and PDFs as clean text, cited answers and structured deep research for apps. | net new | secondary | [lovable.dev](https://docs.lovable.dev/changelog) | Exa |
| f03 | The connector is Managed by Lovable: no Parallel account needed, and each request is charged to the workspace's Lovable credits. | net new | secondary | [lovable.dev](https://docs.lovable.dev/changelog) | Exa |
| f04 | Perplexity reports Fast Search single-call latency of 160 ms at p50 (230 ms at p95). | new detail | primary | [perplexity.ai](https://www.perplexity.ai/hub/blog/photon) | Exa, Tavily |
| f04 | Perplexity says the fast preset cut estimated model-plus-search cost per task by 68% vs its default preset across six agentic benchmarks, at comparable quality. | new detail | primary | [perplexity.ai](https://www.perplexity.ai/hub/blog/photon) | Exa, Tavily |
| f04 | Perplexity concedes lower quality for the fast preset: relevance (DCG) 2.21 vs 2.45 and answer availability 56.7% vs 59.6% for the default preset. | new detail | primary | [perplexity.ai](https://www.perplexity.ai/hub/blog/photon) | Exa, Tavily |
| f04 | Shopify's Mikhail Parakhin says Shopify measures all search APIs and Perplexity has become its main one 'on merit' (X, 2026-09-24). | new detail | secondary | x.com post (URL in the local grading data) | Exa |
| f04 | Perplexity Fast Search is now built into Hermes Agent for Nous Portal users; Nous Research says it is free on all Portal tiers. | new detail | primary | x.com post (URL in the local grading data) | Exa |
| f06 | Firecrawl says its valuation rose more than 10x between the Series A and the Series B (amount undisclosed). | new detail | secondary | [globo.com](https://revistapegn.globo.com/startups/noticia/2026/09/firecrawl-fundada-por-brasileiro-nos-eua-capta-us-75-milhoes-para-alimentar-agentes-de-ia.ghtml) | Exa |
| f06 | CTO says Firecrawl has 40 employees and plans to reach 60 by the end of 2026. | new detail | secondary | [globo.com](https://revistapegn.globo.com/startups/noticia/2026/09/firecrawl-fundada-por-brasileiro-nos-eua-capta-us-75-milhoes-para-alimentar-agentes-de-ia.ghtml) | Exa |
| f06 | CTO says Firecrawl is growing about 20% a month. | new detail | secondary | [globo.com](https://revistapegn.globo.com/startups/noticia/2026/09/firecrawl-fundada-por-brasileiro-nos-eua-capta-us-75-milhoes-para-alimentar-agentes-de-ia.ghtml) | Exa |
| f06 | CTO says Firecrawl has more than 40,000 customers in Brazil. | new detail | secondary | [com.br](https://businessmoment.com.br/firecrawl-capta-75-milhoes-escritorio-brasil-ia/) | Exa |
| f06 | Named Brazilian customers: iFood and Nubank. | new detail | secondary | [globo.com](https://revistapegn.globo.com/startups/noticia/2026/09/firecrawl-fundada-por-brasileiro-nos-eua-capta-us-75-milhoes-para-alimentar-agentes-de-ia.ghtml) | Exa |
| f06 | Firecrawl's careers page states a goal of $100M ARR with fewer than 50 people (reported by The Latent with the Series B). | new detail | primary | [firecrawl.dev](https://www.firecrawl.dev/careers) | Exa |
| f06 | Firecrawl homepage (read 2026-09-26) shows the Alexandria catalog at 93 providers, 640 capabilities and 20 categories. | new detail | primary | [firecrawl.dev](https://www.firecrawl.dev/) | Firecrawl |
| f06 | Firecrawl pays providers such as Wikimedia Enterprise; millions of Wikipedia data requests flow through Firecrawl each month. | new detail | primary | [firecrawl.dev](https://www.firecrawl.dev/blog/introducing-alexandria-series-b) | Exa, Parallel, Tavily |
| f06 | The launch catalog includes vertical data beyond papers and code: US property/rental listings, professional profiles, and products and prices. | new detail | secondary | [runtimewire.com](https://runtimewire.com/article/firecrawl-raises-75m-series-b-alexandria-ai-agent-data) | Exa, Parallel |
| f07 | Knowledge coverage includes prediction-market odds and company and government data (for example federal contract outlays), beyond stocks, crypto, weather and sports. | new detail | primary | [you.com](https://you.com/resources/knowledge-parameter-web-search-api) | Tavily |
| f07 | On VerticalRTK (155 questions), accuracy rose from 64.5% with web search alone to 84.2% with Knowledge, adding about 140ms (0.60s to 0.74s). Four web search APIs clustered at 63.9-65.2%. | new detail | primary | [you.com](https://you.com/resources/knowledge-parameter-web-search-api) | Tavily |
| f07 | Named Knowledge sources and partners: Tako (technical partnership), Xignite/S&P Global, AccuWeather, CoinMarketCap and The Economist; You.com says it has hundreds of sources. | new detail | primary | [you.com](https://you.com/resources/knowledge-parameter-web-search-api) | Tavily |
| f07 | You.com teamed up with Coinbase on the Coinbase for Agents launch: an agent pulls financial news from You.com, pays for data with x402, then trades. | net new | primary | x.com post (URL in the local grading data) | Tavily |
| f09 | Gemini 3.8 Live and 3.8 Live Extended Thinking support Grounding with Google Search; Agent Platform also lists 3.8 Live as a Search-grounding model. | net new | primary | [google.dev](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live-extended-thinking) | Exa, Firecrawl, Parallel, Tavily |
<!-- tables:findings_s4:end -->

## See Also
- [Experiment overview](overview.md)
- [Pre-registered design](design.md)
- [Pre-registration and amendments](preregistration.md)
- [Blind judge brief (v1)](judge-brief.md)
- [Blind judge brief (v2)](judge-brief-v2.md)
- Tavily (KB page `competitive/tavily`, not published)
- Parallel (KB page `competitive/parallel-search-api`, not published)
- Firecrawl (KB page `competitive/firecrawl`, not published)
