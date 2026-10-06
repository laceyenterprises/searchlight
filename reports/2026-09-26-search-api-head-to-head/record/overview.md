---
title: "Search-provider experiment (Exa vs Tavily vs Parallel vs Firecrawl)"
doc_type: experiment
delegation: selective
influence_weight: low
staleness_window: 90d
kb_phase: build-out
canonical: false
status: partial
source_last_updated: 2026-09-26
last_updated: 2026-09-27
last_verified:
token_estimate: 3926
confidence_score: 0.72
provenance_reviewed: false
tags: ["experiments", "search-provider-eval", "exa", "tavily", "parallel", "firecrawl", "evaluation"]
provenance_summary: >-
  Overview of a pre-registered, cost-capped experiment run 2026-09-26. It measures how much verified net-new information
  four search APIs add to this KB beyond native research tools, and what each one uniquely surfaces. It links the
  design, the pre-registration and its amendments, the judge briefs, the stage write-ups, the code, and the sanitized
  data.
---
> **Record note (Searchlight, 2026-10-06).** This file is the experiment's own record, copied from the private
> knowledge base where the experiment ran. Links between record files are converted to relative links; links to
> other knowledge-base pages, which are not published, are shown as plain text with the page's path. Nothing else is
> changed unless a further note says so. The Searchlight summary is [REPORT.md](../REPORT.md).
>
> **Further notes.** Two lines that described the credential-manager placeholders in `keys.env.example` are
> reworded, because the file is not published. The Monitors probe (SM) was harvested and paused on 2026-10-06, four
> days early; its result is in [REPORT.md](../REPORT.md#monitors-probe-sm), not in this overview.

> [!summary] Quick Peek
> - Purpose: The overview for the search-provider experiment: the conclusions, the question, the design in brief, status by stage, what's stored, the terms of use, and how to reproduce it.
> - Conclusion: Exa adds the most new information on top of native research (discovery, lists, news). Firecrawl and Parallel lead on pages that need rendering, and Parallel on filling a fixed schema. One provider for everything leaves gaps. See [Conclusions](#conclusions-stages-a-and-b). On the big grounding vendors' evidence (Stage C), Exa found the most, narrowly and within the noise, and Parallel's research agent was the most accurate; see [Stage C](#conclusions-stage-c).
> - Read this when: You're comparing search providers for this KB's research workload, or re-running or extending the experiment.
> - Skip this when: You need a public benchmark claim. This is internal and small-sample, and the owner decided the results won't be published.
> - Key links: [Stage A results](results-stage-a.md); [Stage B results](results-stage-b.md); [Stage C results](results-stage-c.md); [Pre-registration and amendments](preregistration.md); [Design](design.md); Experiments hub (KB page `experiments/README`, not published)
> - Confidence: 0.72. Pre-registered, with blind grading. Lowered for small samples, one run date, analysis choices corrected after review (Amendments 4, 7 and 12), and post-hoc Stage B scoring choices (Amendment 6).

# Search-provider experiment

## Why
> [!cite] Section provenance
> - Source basis: the owner's request (2026-09-26) and [the design](design.md).
> - Confidence: 0.80

The KB was built with native research tools: Claude's web search tool (which Anthropic's Trust Center lists as backed by
Brave and turbopuffer), page fetching, `curl`, and a browser. The owner asked two questions:
- How much new information can Exa add on top of that?
- What do Tavily, Parallel, and Firecrawl uniquely surface on the same workload?

The owner also asked to keep costs small and not to run every job four times.

## Design in brief
> [!cite] Section provenance
> - Source basis: [design.md](design.md) and [preregistration.md](preregistration.md) (Amendments 1–12).
> - Confidence: 0.80

- **A capability matrix, not four copies of every job.**
  - Stage A's cheap probes (S1–S4) run on all four providers.
  - Stage B's list and schema sets (S5, S6) run only on providers that sell those capabilities: Exa (Agent), Parallel
    (FindAll and Task), and Firecrawl (`/agent`).
  - Two Exa-only probes run in Stage B: find-similar (SP) and Monitors (SM).
- **Signal gates cost.** A stop rule sits at S1 q20, a Stage B gate follows Stage A, and each provider has hard caps.
  The owner raised the total cap to $40 for both stages (Amendment 5).
- **Pooled, deduplicated, blind grading, verified on the page.**
  - Judges never see provider names.
  - They verify each counted claim on its source page.
  - Attribution is rejoined only after judging.
  - Since Amendment 4, pools are shuffled with a recorded seed, and credit is per claim: strict credit requires the
    provider's own result page to state the claim.
- **Best-practice parameters for every provider**, from each vendor's current docs, run on the same day. S2 was
  re-run with identical inputs, after the review found that hand-written queries leaked answer terms (D2).

## Conclusions (Stages A and B)
> [!cite] Section provenance
> - Source basis: [Stage A results](results-stage-a.md) (v2 re-grade, strict per-claim credit) and [Stage B results](results-stage-b.md) (after Amendment 7). Every figure is generated from `data/` [observed — 2026-09-26].
> - Confidence: 0.70. Internal only and small-n: one run date, Claude agents as judges, and the post-hoc choices logged in Amendments 4, 6, and 7. The Monitors probe (SM) reports after 2026-10-10.

1. **Exa adds the most new information on top of native research.** This KB had been built with native tools until
   they stopped finding new facts, and Exa still found verified ones:
   - **Questions the KB couldn't answer:** Exa 25 new or correcting claims, Parallel 19, Tavily 11, Firecrawl 10. Exa
     had 11 that no other provider found. Across providers, 18 of the 40 questions got a new answer.
   - **Two-week news:** Exa 20 new dated facts, Tavily 12, Parallel 3, Firecrawl 2.
   - **Building entity lists:** 45 entities new to the KB. Exa found 28 of them (21 found by no one else) at $0.07 per
     new entity. Firecrawl found 19, and Parallel 7 at $1.45 each.
   - **Cost per new fact on the question and news tests:** Exa was cheapest at $0.011, against Parallel's $0.017.
2. **The rivals' unique strengths:**
   - **Pages our native tools couldn't read:** Firecrawl recovered 14 of 15 and Parallel 12, against Exa's 5 (9 with
     its default cache).
   - **Filling a fixed schema:** Parallel was the most accurate and complete, with 113 of 133 cells right against
     Exa's 105. That gap isn't significant, and the price was the same. Firecrawl trailed at 94, with twice the
     false-claim rate.
   - **Firecrawl adds list coverage:** 12 entities no one else found.
3. **Finding a known source:** all four found 16–19 of 20, within noise.
4. **Recommendation for this KB's research:**
   - Exa for discovery, list building, and news sweeps.
   - Firecrawl or Parallel for pages that need rendering.
   - Parallel or Exa for filling a fixed schema.
   - One provider for everything leaves gaps.

## Conclusions (Stage C)
> [!cite] Section provenance
> - Source basis: [Stage C results](results-stage-c.md), generated from `data/stage-c/`, with Amendment 12's post-hoc corrections after the PR #38 review [observed — 2026-09-27].
> - Confidence: 0.55. Internal only and small-n: 24 questions, one run each, Claude agents as judges, and post-hoc corrections.

5. **Hunting for evidence on the big grounding vendors, Exa found the most verified new tier-1 claims, within the
   noise.** Tier-1 claims are figures that enter a sizing line. With Amendment 12's corrections:
   - both arms: Exa 16, Parallel 14, Tavily 13, Firecrawl 9;
   - research: Exa 11, Parallel 10, Firecrawl 5, Tavily 4;
   - search: Exa and Tavily 11 each, Parallel 8, Firecrawl 7. Exa ranks first only on the pre-registered tie-break,
     cost per claim.

   No lead is statistically significant; the tests are post hoc and Holm-adjusted.
   - **Parallel's research agent was the most accurate at the same price:** 0.6% of its items unsupported or false,
     against Exa's 4.6%, both at $0.10 a run.
   - **Tavily's and Firecrawl's research agents trailed.** Tavily's had the highest error rate (18.1%), and
     Firecrawl's hit its budget on 5 of the 24 questions.
   - **"Found by no other provider"** (Exa 6, Parallel 4, Tavily 4, Firecrawl 0) rests on the judges' support lists.
   - **None of the four big vendors discloses** a grounding volume or a search-tool revenue.
   - As judged, before the corrections, Exa's lead looked wider (research 30 to 19, search 26 to 16). The page shows
     both.

## Status by stage
> [!cite] Section provenance
> - Source basis: the run records in `data/`, and the ledgers [observed — 2026-09-26].
> - Confidence: 0.80

| Stage | Sets | Providers | Status | Write-up |
|---|---|---|---|---|
| A | S1 known unknowns (40), S2 cited-source recall (20), S3 pages native tools couldn't read (15), S4 two-week freshness (10) | All four | Complete. Graded blind twice (v1, then the v2 re-grade of Amendment 4). | [Stage A results](results-stage-a.md) |
| B | S5 entity lists (3), S6 schema fill (20 companies × 8 fields), SP find-similar (10), SM Monitors (5, to 2026-10-10) | Exa, Parallel, Firecrawl (SP and SM: Exa only) | S5 and S6 complete and graded blind; SP complete and graded (not blind, since only Exa ran). Reviewed and remediated (Amendment 7). SM runs until 2026-10-10, then gets graded and its monitors are deleted. | [Stage B results](results-stage-b.md) |
| C | SV big vendors' evidence (24: Google, Anthropic, OpenAI and Microsoft, six facets each) | All four, in a search arm and a research arm | Complete. Graded blind by eight judges (Amendments 8–11). Reviewed and corrected post hoc (Amendment 12). | [Stage C results](results-stage-c.md) |

## Terms of use and owner decisions
> [!cite] Section provenance
> - Source basis: the vendors' terms, read 2026-09-26: [Exa ToS PDF](https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf), [Tavily terms](https://www.tavily.com/terms), [Parallel customer terms](https://parallel.ai/customer-terms), [Firecrawl terms](https://www.firecrawl.dev/terms-of-service).
> - Owner decisions: 2026-09-26.
> - Confidence: 0.75 (a plain reading, not legal advice)

- **Parallel.** Its customer terms §2(c)(viii) restrict creating or giving third parties the results of benchmark
  tests or other evaluations of its services without prior written consent. They also bar using its output for any
  competitive purpose [extracted — 2026-09-26](https://parallel.ai/customer-terms).
- **Tavily.** Its terms bar using the services to build a competitive product or to compete with Tavily
  [extracted — 2026-09-26](https://www.tavily.com/terms).
- **Exa and Firecrawl.** No benchmarking clause was found. Exa's §4.2 restricts copying and distributing output, which
  is why no vendor text is committed [extracted — 2026-09-26](https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf).
- **The owner's position (2026-09-26).** The owner is a customer evaluating providers for its own use, not a
  competitor, and the results won't be published. On that basis the owner asked to run every arm, Parallel included.
  Treat every result here as internal only.

## Results at a glance (Stage A, v2 re-grade)
> [!cite] Section provenance
> - Source basis: generated from `data/stage-a/` by `code/make_tables_a.py`. See [Stage A results](results-stage-a.md) for the method, the broad-credit numbers, the v1 comparison, and the findings.
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

## Results at a glance (Stage B)
> [!cite] Section provenance
> - Source basis: generated from `data/stage-b/` by `code/make_tables_b.py`. See [Stage B results](results-stage-b.md) for each list, the S6 fields, costs, caveats, and the findings.
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

<!-- tables:b_total:start -->
At most $18.22 for Stage B and $5.07 for Stage A, or $23.29 of the owner's $40 cap. This values credits at the most they cost (Tavily $0.008, Firecrawl $0.005), as the cap guard did; at Firecrawl's Scale rate the total is $19.52. The Monitors probe adds at most $1.05 by 2026-10-10. The guard refused 1 run, Parallel's S5c, before the cap correction; it ran after.
<!-- tables:b_total:end -->

## What gets committed
> [!cite] Section provenance
> - Source basis: `code/export_data.py` and `code/.gitignore` [observed — 2026-09-26].
> - Confidence: 0.85

- **Code, sets, and briefs:** in `code/`. `keys.env.example` held only credential-manager placeholders (Searchlight: the file is omitted; see [reproduce.md](../reproduce.md)). Real keys are read at
  run time through `op run` and never written to disk.
- **Data:** in `data/<stage>/`. `export_data.py` uses an allowlist that keeps:
  - request parameters, status, latency, and cost;
  - for each result, its rank, URL, domain, published date, and text length;
  - crawl status codes;
  - our own pools (URLs only), attribution maps, judge verdicts, and scores;
  - for Stage B (Amendment 5), entity names, URLs, cited URLs, and costs, through a per-file allowlist. The S6 cell
    values and S5 claimed fields that the providers wrote are stripped from the pools; the verdicts on them stay.
    Judge notes may quote short fragments (under 15 words) of pages or provider values.
  - for Stage C, `code/stage_c/score_c.py` writes the record: judged claims and verdicts, the sanitized judges' files,
    the attribution maps, the correction layer and run metadata. It uses `export_data.py`'s social-URL rule and
    refuses to write an unhashed social URL. The pools, whose excerpts are vendor output, stay local.
- **Never exported:** page titles, snippets, page text, extracted content, and raw responses. Social-media profile and
  post URLs carry people's names, so their paths are replaced with a stable hash.
- **Local copies.** The local run folders keep raw vendor output until grading and its review end; then the owner
  deletes it (Amendment 7). The owner
  chose to keep the sanitized run data in the repo (2026-09-26).
- **Verified facts** the experiment turns up go into canonical leaves through a separate PR, cited to their primary
  pages.

## Reproduce
> [!cite] Section provenance
> - Source basis: the scripts in `code/` and `code/stage_c/` [observed — 2026-09-27].
> - Confidence: 0.80

```bash
cd experiments/search-provider-eval/code
cp keys.env.example keys.env        # one line per provider key (Searchlight: see ../reproduce.md)
op run --env-file keys.env -- python3 harness.py --set S2 --limit 2 --providers exa             # smoke test
op run --env-file keys.env -- python3 harness.py --set S1 --limit 20                            # S1 q01–q20
op run --env-file keys.env -- python3 harness.py --set S1 --offset 20 --limit 20                # S1 q21–q40
python3 grade_prep.py <kb checkout at the run's baseline>   # S2/S3 scripted scores, v1 pools
python3 pools_v2.py <kb checkout at the baseline commit>    # v2 pools: shuffled, experiments/ excluded
# Judge each grading/v2/*_pool.json with judge-brief-v2.md, saving grading/v2/<chunk>_judge.json, then:
python3 score_v2.py                                         # strict and broad per-claim credit, S4 facts and events
python3 make_tables_a.py ../results-stage-a.md ../README.md # regenerate every Stage A table from the data
op run --env-file keys.env -- python3 diag_stageA.py D2     # post-hoc diagnostics D1–D3
op run --env-file keys.env -- python3 harness_b.py --set S5,S6 --pairs S5:exa,S6:exa   # Stage B, by set and provider
op run --env-file keys.env -- python3 monitors_b.py poll    # SM results; `delete` ends the probe
python3 grade_prep_b.py S6 && python3 grade_prep_b.py S5     # Stage B blind pools and attribution (add --v2 for the fixed matcher)
# Judge each grading/b/*_pool.json with judge-brief-b.md, saving grading/b/<pool>_judge.json, then:
python3 score_b.py                                          # or: python3 score_b.py --data ../data/stage-b
python3 make_tables_b.py ../results-stage-b.md ../README.md # add --data ../data/stage-b to rebuild from the committed data
python3 export_data.py . stage-a                            # sanitized export into ../data/stage-a (stage-b likewise)
cd stage_c                                                  # Stage C: the big vendors' evidence (Amendments 8–12)
op run --env-file ../keys.env -- python3 harness.py --set SV              # search arm (C1)
op run --env-file ../keys.env -- python3 harness_research.py              # research arm (C2)
python3 grade_prep_c.py                                     # blind pools and attribution (seed 20260927)
# Judge each grading/C/pool_<id>.json with judge-brief-c.md, saving grading/C/judged_J<n>.json, then:
python3 score_c.py                                          # or rebuild from the record: python3 score_c.py --data ../../data/stage-c
python3 make_tables_c.py ../../results-stage-c.md           # add --data ../../data/stage-c to rebuild from the committed record
```

- Run folders (`runs/`, `grading/`, judge scratch) and `keys.env` are gitignored. Stage C's `--data` commands need
  neither: they rebuild its scores and every table from the committed record.
- A re-run will give different results, because the web and the indexes change.

## Files
| File | What it is |
|---|---|
| [design.md](design.md) | The pre-registered design: sets, parameters, metrics, stop rules, gates, and budget |
| [preregistration.md](preregistration.md) | SHA-256 hashes of the frozen files, and Amendments 1–12 |
| [judge-brief.md](judge-brief.md), [judge-brief-v2.md](judge-brief-v2.md), [judge-brief-b.md](judge-brief-b.md), [judge-brief-c.md](judge-brief-c.md) | The v1 blind judge brief, the v2 re-grading brief (Amendment 4), the Stage B brief, and the Stage C brief (Amendment 8) |
| [results-stage-a.md](results-stage-a.md) | Stage A results |
| [results-stage-b.md](results-stage-b.md) | Stage B results (S5, S6, SP; SM after 2026-10-10) |
| [results-stage-c.md](results-stage-c.md) | Stage C results: the big vendors' evidence, as judged and corrected (Amendment 12) |
| `code/` | Stage A: `harness.py`, `grade_prep.py`, `pools_v2.py`, `score_v2.py`, `make_tables_a.py`, `score_s1.py`, `score_judged.py`, `diag_stageA.py`. Stage B: `harness_b.py`, `monitors_b.py`, `grade_prep_b.py`, `score_b.py`, `make_tables_b.py`. Shared: `export_data.py`, the configs, and `sets/S1–S6, SP, SM, SV` |
| `code/stage_c/` | Stage C: `harness.py` (byte-identical to Stage A's), `harness_research.py`, `config.json`, `config_c2.json`, `grade_prep_c.py`, `score_c.py`, `make_tables_c.py`, and a copy of `sets/SV.json` |
| `data/stage-a/` | Sanitized run records (including diagnostics), the ledger, logs, pools, attribution, judge verdicts, and scores (v1 and v2) |
| `data/stage-b/` | Sanitized Stage B run records and ledger; pools stripped of provider-written values; attribution, judge verdicts, the post-hoc files of Amendments 6 and 7, and scores |
| `data/stage-c/` | Judged claims and research-item verdicts, the sanitized judges' files, attribution maps and assignment (`grading/`), run metadata with times, Amendment 12's `corrections.json`, and scores as judged and corrected; no vendor text |

## See Also
- Experiments hub (KB page `experiments/README`, not published)
- [Stage A results](results-stage-a.md)
- [Stage B results](results-stage-b.md)
- [Stage C results](results-stage-c.md)
- Tavily (KB page `competitive/tavily`, not published)
- Parallel (KB page `competitive/parallel-search-api`, not published)
- Firecrawl (KB page `competitive/firecrawl`, not published)
- Exa Search API (KB page `features/search-api`, not published)
