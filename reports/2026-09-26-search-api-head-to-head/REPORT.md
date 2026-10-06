# Search API head-to-head: Exa, Tavily, Parallel and Firecrawl on a research workload (2026-09-26)

This study called four search APIs directly, not through a coding agent. It asked how much verified new information
each adds to a research knowledge base that had already been built with an agent's built-in search tools until they
stopped finding new facts, and what each provider surfaces that the others don't. It was pre-registered and
cost-capped, results were pooled and graded blind, and every counted claim was verified on its source page. Stages A
and B ran on 2026-09-26, Stage C on 2026-09-27, and an Exa-only Monitors probe ran daily from 2026-09-27 to
2026-10-06. The full record (design, pre-registration with twelve amendments, judge briefs and stage write-ups), the
code and the sanitized data are published in this directory.

> **Context.** The experiment was run while building a go-to-market knowledge base about Exa, one of the four providers
> tested. Its questions come from that knowledge base's open questions about Exa's market and competitors, so the
> workload reflects what that knowledge base needed. The design added workloads where Exa might be weak on purpose
> (freshness, and pages that need rendering). Weigh the results with that in mind; the record, code and data are
> here so readers can check them.

## Headline

- **On top of built-in search, Exa added the most verified new information.** On 40 questions the knowledge base
  couldn't answer, verified new or correcting claims under strict credit: Exa 25, Parallel 19, Tavily 11, Firecrawl 10,
  with 11 of Exa's found by no other provider. On two weeks of company news: Exa 20 new dated facts, Tavily 12,
  Parallel 3, Firecrawl 2. Building three entity lists: Exa returned 28 entities new to the knowledge base (21 that no
  other provider returned), Firecrawl 19 and Parallel 7.
- **The other providers led elsewhere.** On pages the built-in tools couldn't read, Firecrawl recovered 14 of 15,
  Parallel 12, Tavily 10 and Exa 5. Filling a 20-company, eight-field schema, Parallel was right on 113 of 133
  ground-truth cells, Exa on 105 and Firecrawl on 94; the Parallel–Exa gap is not significant, at the same price.
- **Finding a source the knowledge base already cites was a tie:** 16 to 19 of 20 for all four, on identical inputs.
- **On evidence about four big grounding vendors (Stage C), no lead is significant.** Verified new tier-1 claims across a
  search arm and a research arm, after the post-hoc corrections of Amendment 12: Exa 16, Parallel 14, Tavily 13,
  Firecrawl 9. Parallel's research agent was the most accurate: 0.6% of its checkable items were unsupported or false,
  against Exa's 4.6%, Firecrawl's 2.7% and Tavily's 18.1%.
- **Exa's Monitors caught real competitor changes, with some noise.** Over 49 daily runs on five competitors' pricing and changelog pages, they reported 20 changes: 14 confirmed, 5 false positives. 13 were real, dated pricing or product changes, 12 of them missing from the knowledge base or only partly recorded there.
- **One provider for everything leaves gaps.** For this workload the record recommends Exa for discovery, list building
  and news sweeps; Firecrawl or Parallel for pages that need rendering; and Parallel or Exa for filling a fixed schema.

## What was tested

Each provider ran with parameters taken from its own best-practice documentation and frozen before the first call, on
the same day, with the same top 10. The design is a capability matrix rather than four copies of every job: Stage A's
cheap probes ran on all four providers, and Stage B's list and schema sets ran only on providers that sell those
capabilities. Tavily sat out S5 and S6 because it sells no list-building or structured-extraction product. See
[methodology](methodology.md) for the endpoints, grading and amendments.
| Set | Stage | Size | What it tests | Providers |
| --- | --- | --- | --- | --- |
| S1 known unknowns | A | 40 questions | Can a provider answer what the built-in research couldn't? | All four |
| S2 known knowns | A | 20 claims | Is the primary source the knowledge base already cites in the top 10? | All four |
| S3 blocked sources | A | 15 pages | Can it read pages the built-in tools couldn't (JavaScript, rate limits, PDFs)? | All four |
| S4 freshness | A | 10 companies | New, dated facts from the last 14 days | All four |
| S5 entity lists | B | 3 lists | Precision, and entities new to the knowledge base | Exa, Parallel, Firecrawl |
| S6 schema fill | B | 20 companies × 8 fields | Accuracy and completeness on a fixed schema | Exa, Parallel, Firecrawl |
| SP find-similar | B | 10 homepages | New competitor domains | Exa |
| SM Monitors | B | 5 monitors, daily | Does a monitor surface real, dated pricing or product changes? | Exa |
| SV big vendors' evidence | C | 24 questions × 2 arms | Verified new evidence on four grounding vendors, by search and by research agent | All four |
## Stage A: four providers, cheap probes

Strict credit counts a claim only when the provider's own result page states it (Amendment 4). S2 was re-run with
identical inputs after a review found that hand-written queries leaked answer terms. The
[Stage A write-up](record/results-stage-a.md) has broad credit, the first grading round and the diagnostics.
| Stage A (v2 re-grade, strict credit) | Exa | Tavily | Parallel | Firecrawl |
| --- | --- | --- | --- | --- |
| S1 new or correcting claims, strict (known unknowns, 40 questions) | 25 | 11 | 19 | 10 |
| … no other provider supported | 11 | 2 | 4 | 0 |
| S4 new dated facts, strict (10 companies, two weeks) | 20 | 12 | 3 | 2 |
| S3 pages recovered that native tools couldn't read (of 15) | 5 | 10 | 12 | 14 |
| S2 cited sources found in the top 10, identical inputs (of 20) | 18 | 16 | 19 | 17 |
| Stage A run spend | $0.50 | 146 credits ($1.17) | $0.37 | 402 credits ($0.30–$2.01) |
| Median latency, ms | 1,230 | 2,995 | 2,343 | 1,171 |
## Stage B: lists, schema fill and find-similar

S5 counts entities verified against the list's criteria and new to the knowledge base, under the fact-level rule; the
stricter entity-level rule gives Exa 27, Firecrawl 18 and Parallel 3, in the same order. S6 compares cells with the
knowledge base's verified ground truth. Parallel's spend is at list price; Firecrawl's credits are shown as a range
across plans. The [Stage B write-up](record/results-stage-b.md) has each list, each field and the review fixes
(Amendments 6 and 7).
| Stage B | Exa | Parallel | Firecrawl |
| --- | --- | --- | --- |
| S5 entities verified and new to the KB (3 lists, fact-level rule) | 28 | 7 | 19 |
| … no other provider returned it | 21 | 5 | 12 |
| S5 precision: entities meeting every criterion | 28/36 (77.8%) | 13/21 (61.9%) | 19/23 (82.6%) |
| S6 right on KB ground truth, strict (of 133 cells) | 105/133 (78.9%) | 113/133 (85.0%) | 94/133 (70.7%) |
| S6 cells filled (of 160) | 149/160 (93.1%) | 156/160 (97.5%) | 157/160 (98.1%) |
| S6 false-claim rate | 7/149 (4.7%) | 6/156 (3.8%) | 16/157 (10.2%) |
| S6 cells where it added a verified new fact (of 40) | 30 | 31 | 25 |
| Stage B spend | $4.09 | $12.16 (list price) | 393 credits ($0.29–$1.97), plus 5 free runs |
Find-similar (SP, Exa only) is a cheap sweep for long-tail competitors, but it needs triage: 50 of the 61 new
domains were directories, profiles, clones or unrelated sites. It was graded, but not blind, since only Exa ran.
| Exa find-similar, 10 competitor homepages | Count |
| --- | --- |
| Results returned | 100 |
| Distinct registrable domains | 69 |
| … already cited in the KB | 8 |
| … new to the KB | 61 |
| New domains: relevant vendors (search, SERP, crawling, or web-data APIs) | 11 |
| New domains: profiles, directories, or reviews of a seed company | 39 |
| New domains: clones or unrelated | 11 |
| Relevant vendors the KB baseline already covers | 0 |
| Spend | $0.07 |
## Stage C: evidence on the big grounding vendors

Twenty-four pre-registered questions asked for evidence on four supply lines a market sizing couldn't anchor: Google
grounding, Anthropic's and OpenAI's web search tools, and Microsoft Grounding with Bing. Each provider ran a search
arm and a research-agent arm, and eight blind judges verified every claim on its page. A tier-1 claim is a figure that
enters a line's arithmetic: a volume, a revenue figure or share, a denominator, or a price. After a review, Amendment
12 (post hoc) added a claim-level correction layer for novelty, tiers, duplicates and Tavily's cap-refused runs; the
[Stage C write-up](record/results-stage-c.md) shows the as-judged figures beside the corrected ones.
| Provider | New tier-1 claims (either arm) | …found by no other provider | All new claims | Cost, both arms | Cost per new tier-1 claim |
| --- | --- | --- | --- | --- | --- |
| Exa | 16 | 6 | 207 | $2.57 | $0.16 |
| Parallel | 14 | 4 | 131 | $2.52 | $0.18 |
| Tavily | 13 | 4 | 79 | $3.96–$6.34 | $0.30–$0.49 |
| Firecrawl | 9 | 0 | 126 | $1.16–$7.75 | $0.13–$0.86 |
By arm, corrected. The research error rate is the share of a research agent's checkable items whose cited page didn't state them or contradicted them.
| Provider | Arm | New tier-1 claims | …found by no other arm | All new claims | Questions with a new claim (of 24) | Useful new tier-1 (2–3) | Research error rate | Median time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Exa | search | 11 | 2 | 100 | 20 | 6 | — | 1.7 s |
| Parallel | search | 8 | 3 | 75 | 20 | 6 | — | 3.1 s |
| Tavily | search | 11 | 3 | 58 | 21 | 5 | — | 3.2 s |
| Firecrawl | search | 7 | 0 | 57 | 18 | 3 | — | 1.3 s |
| Exa | research | 11 | 2 | 158 | 24 | 8 | 4.6% (21 of 455) | 86.1 s |
| Parallel | research | 10 | 1 | 84 | 17 | 6 | 0.6% (2 of 331) | 107.0 s |
| Tavily | research | 4 | 1 | 36 | 13 | 1 | 18.1% (19 of 105) | 31.4 s |
| Firecrawl | research | 5 | 0 | 85 | 18 | 3 | 2.7% (8 of 298) | 71.7 s |
Is the lead real? Exact two-sided sign tests over the 24 questions, chosen after the results were known, with
Holm's adjustment for the six comparisons and question-level bootstraps:
| Arm | Leader | Against | New tier-1 (leader / other) | Questions ahead / behind | Sign test p | Holm-adjusted p (6 tests) | Bootstrap 95% CI of the difference |
| --- | --- | --- | --- | --- | --- | --- | --- |
| search | Exa | Tavily | 11 / 11 | 3 / 3 | 1.000 | 1.000 | -7 to 7 |
| search | Exa | Parallel | 11 / 8 | 3 / 2 | 1.000 | 1.000 | -5 to 12 |
| search | Exa | Firecrawl | 11 / 7 | 4 / 1 | 0.375 | 1.000 | -1 to 10 |
| research | Exa | Parallel | 11 / 10 | 4 / 2 | 0.688 | 1.000 | -5 to 6 |
| research | Exa | Firecrawl | 11 / 5 | 5 / 1 | 0.219 | 1.000 | 0 to 13 |
| research | Exa | Tavily | 11 / 4 | 5 / 1 | 0.219 | 1.000 | 0 to 15 |
## Monitors probe (SM)

Five Exa Monitors watched competitors' pricing and changelog pages once a day. The pre-registered question: does a
monitor surface a real, dated pricing or product change on the competitor's own page within the knowledge base's 30-day
staleness window, and was the change already in the knowledge base?

- **Deviation from the plan.** The probe was planned to run to 2026-10-10 and then delete its monitors. It was harvested
  and the monitors paused on 2026-10-06, four days early, so it covers 49 daily runs rather than about 70. The
  monitors were paused, not deleted.
- **What the monitors reported.** 14 of 49 runs reported at least one change, 20 changes in all.
  Monitors report only what changed since their previous run.
- **Graded result.** Of the 20 reported changes, 14 were confirmed against the vendor's page and archived snapshots, 5 were false positives and 1 could not be settled either way. 13 were real, dated pricing or product changes, and 12 of those were missing from the knowledge base or only partly recorded there. 3 of 5 monitors answered the pre-registered question yes: Parallel, Firecrawl and Perplexity API.
- **Where it worked and where it didn't.** Perplexity's monitor was the strongest: its changelog carries exact timestamps, and every confirmed change fell inside its run window. Firecrawl's monitor reported a security feature added, removed and added again on its pricing page; the page was unchanged in every archived capture, so those reports are noise. Both of Brave's reports were false positives: prices were unchanged, and the watched pages had started serving a version without prices. Tavily's monitor reported nothing, and its changelog had nothing new.
- **Misses were not measured.** The grader noticed one: a Firecrawl changelog entry from 2026-09-29 was never reported. Per-change verdicts, evidence links and notes are in [the grading record](data/stage-b/runs/SM/grading.json) and [its write-up](data/stage-b/runs/SM/grading.md).
- **How it was graded.** One Claude agent, not blind (only Exa ran), checked each reported change against the vendor's
  current page and Internet Archive snapshots from before and after the run, and searched the knowledge base for it.
| Monitor (competitor pages watched) | Daily runs | Runs reporting a change | Changes reported | Confirmed real | Real, dated pricing or product change | …missing from the KB or partial |
| --- | --- | --- | --- | --- | --- | --- |
| Tavily | 10 | 0 | 0 | 0 | no (0) | no (0) |
| Parallel | 10 | 3 | 4 | 4 | yes (3) | yes (2) |
| Firecrawl | 10 | 4 | 6 | 3 | yes (3) | yes (3) |
| Perplexity API | 9 | 6 | 8 | 7 | yes (7) | yes (7) |
| Brave Search API | 10 | 1 | 2 | 0 | no (0) | no (0) |
## Spend

Stages A and B together cost at most $23.29 of the $40 cap, valuing credits at the most they cost (Stage A $5.07,
Stage B $18.22). Stage C's costs are in its provider table above; Exa's are billed, Parallel's are list-price estimates
and Tavily's and Firecrawl's credits are ranges across plans. The Monitors probe made 49 runs, at $15 per 1,000 runs
on Exa's price list.

## Read this before comparing providers

- **Small samples, one run.** Each item ran once on one day. The web and the indexes change, so a re-run will differ.
- **Model judges.** Claude agents graded blind to the provider and verified every counted claim on its page; no human
  graded the sets.
- **Post-hoc changes are logged, not hidden.** Amendments 4, 6, 7 and 12 changed scoring after results were seen. The
  record keeps the earlier figures beside the corrected ones, and the [pre-registration](record/preregistration.md)
  lists every amendment.
- **New means new to one knowledge base.** Novelty is measured against the knowledge base the questions came from.
- **Direct APIs, tuned parameters.** Unlike the agent benchmarks, where each provider ran through its MCP server at
  default settings, this study called each API directly with its best-practice parameters. Results do not transfer
  between the two setups.
- **Vendor terms.** The experiment was first run as an internal evaluation. The record's overview summarises each
  vendor's terms on benchmarking as read on 2026-09-26 ([terms](record/overview.md#terms-of-use-and-owner-decisions)).
- **What is not published.** Page text, titles, snippets, vendor-written values and raw responses are not published;
  social-media URLs are hashed. Raw responses were deleted after grading (Amendment 7), so grading cannot be replayed
  exactly. Every published table rebuilds from `data/` except Stage A's S3 rows, which need page text; their per-page
  outcomes are in [the scripted summary](data/stage-a/grading/summary_stageA_scripted.json).

## Record and data

- The experiment's [overview](record/overview.md), [design](record/design.md) and
  [pre-registration with Amendments 1–12](record/preregistration.md).
- Judge briefs: [first round](record/judge-brief.md), [re-grade](record/judge-brief-v2.md),
  [Stage B](record/judge-brief-b.md) and [Stage C](record/judge-brief-c.md).
- Stage write-ups: [Stage A](record/results-stage-a.md), [Stage B](record/results-stage-b.md) and
  [Stage C](record/results-stage-c.md), each with every table and the verified findings.
- Code: the Stage A harness [code/harness.py](code/harness.py), Stage B [code/harness_b.py](code/harness_b.py), Stage C
  [code/stage_c/harness_research.py](code/stage_c/harness_research.py), the exporter
  [code/export_data.py](code/export_data.py) and the Monitors harvest [code/monitors_harvest.py](code/monitors_harvest.py).
- Scores: [Stage A](data/stage-a/grading/v2/scored_v2.json), [Stage B](data/stage-b/grading/b/scored_b.json),
  [Stage C corrected](data/stage-c/scores_corrected.json) and [as judged](data/stage-c/scores.json); the Monitors
  [harvest](data/stage-b/runs/SM/summary.json) and [grading](data/stage-b/runs/SM/grading.json).

Read the [summary data](summary.json), [calibration record](calibration.json), [methodology](methodology.md) and [reproduction steps](reproduce.md).
