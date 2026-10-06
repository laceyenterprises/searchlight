---
title: "Search-provider experiment: pre-registration and amendments"
doc_type: experiment
delegation: full
influence_weight: low
staleness_window: 90d
kb_phase: build-out
canonical: false
status: partial
source_last_updated: 2026-09-26
last_updated: 2026-09-27
last_verified:
token_estimate: 7037
confidence_score: 0.85
provenance_reviewed: false
tags: ["experiments", "search-provider-eval", "preregistration"]
provenance_summary: >-
  File hashes recorded before the first paid call, with dated amendments. Amendments 1–3 are run-time fixes; no question,
  metric, or stop rule changed. Amendment 4 logs analysis choices found in the PR #25 review and fixes the v2
  re-grading rules before any v2 result. Amendment 5 freezes Stage B, with an S5 addendum and a cap correction.
  Amendment 8 freezes Stage C, the big vendors' evidence (2026-09-27); Amendments 9–11 log its run-time changes.
  Amendments 6 and 7 log post-hoc Stage B choices; Amendment 12 logs the Stage C corrections after the PR #38 review.
---
> **Record note (Searchlight, 2026-10-06).** This file is the experiment's own record, copied from the private
> knowledge base where the experiment ran. Links between record files are converted to relative links; links to
> other knowledge-base pages, which are not published, are shown as plain text with the page's path. Nothing else is
> changed unless a further note says so. The Searchlight summary is [REPORT.md](../REPORT.md).

> [!note] How to check
> - The hashes are SHA-256 prefixes (`shasum -a 256 <file> | cut -c1-16`). The files now in `code/` hash as follows: `harness.py` c267493cf7c69493, `config.json` 9713442590dc5e2d, `sets/S1.json` 52acda93f6de9a5f, `sets/S2.json` 173f96b19f5fdca1, `sets/S3.json` 4fac7e3659d0a24a, `sets/S4.json` 77c85e63743a0e43, `JUDGE_BRIEF_V2.md` d9051d79e3ee6b84 (record copy: judge-brief-v2.md), `pools_v2.py` 5a0419d784d421aa, `score_v2.py` 4f33e099aff38a2b, `diag_stageA.py` dbfd3d2fac6ee529, `harness_b.py` b3e4eee3298f545a, `config_b.json` dd991f1b983b24e4, `sets/S5.json` bf7a4c2b3782c385, `sets/S6.json` a1cf44e3cc7fdbfa, `sets/S6_truth.json` 2d4f6d36e0eba4f7, `sets/SP.json` 03ff654be31f4cfd, `sets/SM.json` 3927fd6f499e2491, `monitors_b.py` 404bf3b67f977cfd, `JUDGE_BRIEF_B.md` f7e337bb4198b45a (record copy: judge-brief-b.md), `grade_prep_b.py` d052db5664785e7f. `score_b.py` and `make_tables_b.py` postdate unblinding (Amendment 6), and all four Stage B scripts changed in Amendment 7.
> - The S1–S4 sets match the frozen hashes. `harness.py` and `config.json` match Amendment 3 and Amendment 2. The Stage B files match Amendment 5, its S5 addendum, and the cap correction (`config_b.json`). `grade_prep.py` and `export_data.py` changed after Amendment 4 (the experiments/ exclusion, the allowlist export scoped by stage); their current code is in `code/`.
> - The frozen pre-run versions of `harness.py` and `config.json` weren't kept. The amendments describe each change. The Stage C files match Amendments 8–11, except `score_c.py`, `make_tables_c.py` and `export_data.py`, which changed after unblinding (Amendment 12).

# Pre-registration log (2026-09-26 15:07 PDT)

Frozen before the first API call. SHA-256 of each file:

- `DESIGN.md` 5f3aa89b59ad14d3
- `config.json` 47722b251efafdfb
- `sets/S1.json` 52acda93f6de9a5f
- `sets/S2.json` 173f96b19f5fdca1
- `sets/S3.json` 4fac7e3659d0a24a
- `sets/S4.json` 77c85e63743a0e43
- `harness.py` 02100fd9b697b5d7

## Amendment 1 (after the 1-item smoke test, before the main run)

- Parallel: `max_results` and `source_policy` moved into `advanced_settings` (the API rejected them at top level, HTTP 422).
- Firecrawl: result scrapes cap PDF parsing at 3 pages (`parsers: [{type: pdf, maxPages: 3}]`) after one search plus three scrapes cost 33 credits. Blocked-page fetches (S3) keep default parsing.
- No query, set, or metric changed. New hashes: harness.py 2eed4e7bf016bb31, config.json b094caad7cdccdc2.

## Amendment 2 (diagnostics, before the main run)

- Firecrawl charges 30 credits to scrape an X post (measured: `creditsUsed: 30`, basic proxy) against 1 credit for a normal page. Result scrapes (S1, S2) now skip social and login-walled domains (x.com, twitter.com, linkedin.com, facebook.com, instagram.com, tiktok.com, threads.net, youtube.com, reddit.com); those results keep Firecrawl's own snippet. S3 fetches are unchanged.
- Diagnostic spend: 2 extra Firecrawl scrapes (1 + 30 credits) and the smoke test, all counted in the ledger.
- New hashes: harness.py 98eb867821a7ad11, config.json 9713442590dc5e2d.

## Amendment 3 (after Stage A, before grading)

- Parallel Extract: `full_content` must sit inside `advanced_settings`. The Stage A S3 run sent it top-level and got HTTP 422 on all 15 fetches, so Parallel's S3 is re-run with the corrected body (one diagnostic call each for the error and the fix, counted in the ledger). No other set is re-run. New harness.py hash c267493cf7c69493.

## Amendment 4 (Stage A re-grading, logged 2026-09-26 17:20 PDT, after the PR #25 review and before any v2 result)

The review of PR #25 found analysis choices that were never pre-registered, and that tilt toward Exa. This amendment
names each one. The re-grading rules below were fixed before any re-graded result existed. The v1 results stay in the
record as the superseded first pass.

**Analysis choices made after the freeze, and not logged until now:**
- S1 credited a provider for any support result on a question. The design credits a claim to each provider that
  surfaced it.
- The S4 facts were split by hand after unblinding, and no script was committed.
- The S3 result for CodeRabbit (b04) was hand-corrected. Its URL redirects to the homepage for every provider.
- Items scoring usefulness 2 or more were counted regardless of the novelty label.
- The judge brief (a9104578050ff7cd, 15:11:58) and `grade_prep.py` were finalized after the first paid call
  (15:07:10) and were never hashed. The frozen versions of `harness.py` and `config.json` weren't kept. The
  amendments above describe each change.

**Re-grading rules (v2):**
- **Blinding by shuffle.** Pools are the same as v1, but each pool's order is shuffled with a recorded seed
  (`random.Random('20260926-<set>-<item id>')`) and its rids renumbered. In v1, Exa's results always filled slots
  r1–r10.
- **Novelty baseline.** One checkout of the base branch at 933777c, which includes PRs #23 and #24, with
  `experiments/` excluded. v1 used a snapshot from before #24.
- **Per-claim credit.**
  - The judge splits each S1 answer into claims, and each S4 event into facts.
  - For each one, the judge lists the results whose page states it (`support_rids_full`) and the results that
    support only part of it (`support_rids_partial`).
  - Strict credit uses full support only. Broad credit adds partial support. Both are reported.
- **S4 facts** are split by the judge, blind, and labeled `net_new`, `new_detail`, or `already_in_kb`. Results are
  reported at both fact level and event level.
- **Judges.** Six blind judge agents: S1 in four chunks of ten questions, S4 in two chunks of five companies.
- **Stop rule check.** Applied to q01–q20 under both credit rules, with the result reported whichever way it falls.

**Post-hoc diagnostics** (new API calls, counted in the ledger, changing no set):
- **D1.** Exa `/contents` with default cache behavior on the five S3 pages whose forced live crawl errored.
- **D2.** S2 with identical inputs for all four providers: the question text only. This removes the hand-written
  Parallel queries and Firecrawl keyword queries that leaked answer terms.
- **D3.** Firecrawl S4 f01 with and without `tbs`.

**Data and code fixes:**
- `export_data.py` becomes an allowlist. It drops titles and replaces social-profile and post URL paths with a hash.
- `grade_prep.py` skips `experiments/` when it builds the KB URL baseline.

**Hashes (SHA-256 prefixes):**
- `JUDGE_BRIEF_V2.md` d9051d79e3ee6b84 (17:01:02)
- `pools_v2.py` 5a0419d784d421aa (17:00:29)
- `score_v2.py` 4f33e099aff38a2b (17:08:21)
- `score_s1.py` 22326504a08f5e35
- `diag_stageA.py` dbfd3d2fac6ee529
- `grade_prep.py` 1ef597aa93a1d794
- `export_data.py` 7afa33ff27b3c132

## Amendment 5 (Stage B, frozen 2026-09-26 17:20 PDT before its first paid call)

**Owner decisions (2026-09-26):**
- Go on Stage B, with a total cap of $40 for Stage A plus Stage B (credits valued at list).
- Keep the run data in the repo.
- Run all the probes, including Parallel and the Monitors probe.
- The owner is a customer evaluating providers for its own use, not a competitor, and the results won't be
  published. This was recorded after the terms check:
  - Parallel's customer terms §2(c)(viii) restrict creating or sharing benchmark results without consent;
  - Tavily's terms restrict use to compete with Tavily;
  - Exa's and Firecrawl's terms have no benchmarking clause.
- Firecrawl usage past its free plan spills over to a paid plan.
- The Monitors webhook subscribes only to `monitor.deleted` and points at a placeholder path. Exa rejected
  example.com, so it's a non-existent path on exa.ai. Results are polled through the API.

**Scope.**
- S5 (3 lists) and S6 (20 companies × 8 fields) on Exa, Parallel, and Firecrawl.
- SP (find-similar) and SM (five Monitors, daily for two weeks) on Exa.
- Tavily sits out, as designed.

**Parameters.** From each vendor's current docs, read 2026-09-26. Every provider gets one shot, with no preview or
tuning runs.
- **Exa**
  - S5: Agent, `effort: auto`, default $5 cap per run, `outputSchema` (up to 20), `input.exclusion`.
  - S6: Agent, `effort: medium` ($0.10), one run per company with `input.data`.
  - SP: `/findSimilar`, `numResults: 10`, `excludeSourceDomain`. The endpoint is marked deprecated in favor of
    `/search`, and is kept because it's the pre-registered probe.
  - SM: `/monitors`, interval 1d, `numResults: 10`, `includeDomains` per competitor.
- **Parallel**
  - S5: FindAll `core`, `match_limit: 20`, `exclude_list`, and one `base` enrichment for the fields that aren't
    criteria.
  - S6: Task `pro` ($0.10), one run per company, JSON output schema.
- **Firecrawl**
  - `/agent` with a JSON schema, the default model and effort.
  - S5: one run per list, `maxCredits: 2500`, exclusions in the prompt.
  - S6: four runs of five companies, `maxCredits: 1000`.
  - The first five runs a day are free.
- **Same inputs everywhere:** each list's objective, criteria, fields, 20-item cap, and exclusions, and each company's
  name and homepage. S6 is price-matched at $0.10 per company on Exa and Parallel.

**Caps.**
- Per provider: Exa $17.50, Parallel $17.50, Firecrawl 2,500 paid credits.
- Every run reserves its worst-case cost in a file-locked ledger shared across processes, and the harness refuses or
  holds any run that would take Stage A plus Stage B plus in-flight reservations over $40.
- FindAll cost is computed at list price. A FindAll run still active at 60 minutes is cancelled.

**Grading** (blind to provider, using the v2 method: shuffled pools, per-claim strict and broad credit, and the
933777c baseline).
- **S5.**
  - Entities are pooled per list and deduplicated by domain.
  - Each is judged on the page against the criteria (`meets`, `partial`, `fails`, or `unverifiable`) and checked
    against the exclusion list.
  - Metrics: precision; verified novel entities; entities unique to one provider; cost per verified novel entity.
- **S6.**
  - Each distinct value in a cell is judged with its cited URL.
  - Ground-truth cells (`S6_truth.json`: 113 positive and 20 absence cells, built from the KB before any Stage B
    call) are marked `correct`, `partly correct`, or `wrong`. A verified disagreement with the KB counts as a
    correction.
  - Cells without ground truth are marked `verified`, `wrong`, or `unverifiable`.
  - Metrics: accuracy, completeness, verified new cells, false-claim rate, and citation validity.
- **SP.** New domains judged relevant or not.
- **SM.** Results judged at the end as a real, dated change on the competitor's own page (yes or no), and whether it
  was already in the KB.

**Data.**
- Exported Stage B data keeps entity names, URLs, cited URLs, costs, and our verdicts. Provider-written values and
  text are stripped.
- Local raw output is deleted after grading.

**Files (SHA-256 prefixes):**
- `harness_b.py` b3e4eee3298f545a
- `config_b.json` 5850f908fffc0ce8
- `sets/S6.json` a1cf44e3cc7fdbfa
- `sets/S6_truth.json` 2d4f6d36e0eba4f7
- `sets/SP.json` 03ff654be31f4cfd
- `sets/SM.json` 3927fd6f499e2491 (monitors created 17:16, before this text was written; spec hashed before creation)
- `monitors_b.py` 404bf3b67f977cfd
- `sets/S5.json` gets its exclusion lists merged in before any S5 call. Its hash is recorded in an addendum at that
  point.

### Amendment 5 addendum (S5, frozen 2026-09-26 before any S5 call)
- `sets/S5.json` bf7a4c2b3782c385: the exclusion lists are merged in. S5a has 113 entries, S5b 45, and S5c 183 (every entity the
  KB names, plus the ten Stage A subprocessor listings). Each provider gets the same lists: Exa through
  `input.exclusion`, Parallel through `exclude_list`, and Firecrawl in its prompt. The longest Firecrawl prompt is
  9,173 characters, under its 10,000 limit.
- `harness_b.py` b3e4eee3298f545a (unchanged since the Amendment 5 hash).
- **Answer-key conflicts, noted and not edited.** Two S6 ground-truth cells are older than Stage A's verified
  findings: Tavily `hipaa_baa` (the KB says not found; Tavily's MSA DPA §1.8 and AUP bar PHI without written consent)
  and Valyu `last_funding` (the KB says not found; a16z crypto CSX, $500K, March 2024, per aggregators). The frozen
  key stays as it is. Under the pre-registered rule, a provider value that disagrees with the key and is verified on
  its cited page counts as a correction.
- **Run order.** S6 on Exa and Parallel, plus SP, ran first (process 1, 17:21). S5 on all three providers and S6 on
  Firecrawl run in process 2, so Firecrawl's `/agent` calls stay sequential. Both processes share the file-locked
  ledger.
- **Cap correction (00:29 UTC, 2026-09-27).** Parallel's per-provider cap was $17.50, below its pre-registered worst
  case: 3 FindAll lists at $5.20 plus 20 Task runs at $0.10 comes to $17.60. The guard refused Parallel's S5c run.
  The cap is now $18.00 (`config_b.json` dd991f1b983b24e4), still inside the owner's $40 total, which the guard enforces,
  and S5c was re-run for Parallel alone. The refused attempt stays in the log as `skipped_cap`.

## Amendment 6 (post hoc: Stage B scoring, written 2026-09-26 after unblinding)

The runs and the verdicts are unchanged. This amendment logs the analysis choices made after attribution was rejoined.
None of them changed a verdict.

- **Unhashed grading files.** The Stage B judge brief (`JUDGE_BRIEF_B.md`, f7e337bb4198b45a) and `grade_prep_b.py`
  (d052db5664785e7f) were written after the runs. Neither was hashed before judging. File times show the brief last
  changed at 17:53 on 2026-09-26: after the pools were built (17:52) and before the first verdict (18:04).
  `score_b.py` and `make_tables_b.py` were written after unblinding.
- **S5 merge.** The domain dedupe missed two same-company pairs, and the judges flagged both in their notes:
  - Sana (sanalabs.com and sana.ai; S5b-e2 and S5b-e6);
  - Coherence (with and without its domain; S5c-e13 and S5c-e26).

  The scorer merges each pair. That makes 70 pooled entities instead of 72, and 42 new entities instead of 44. It
  also lowers Exa's and Firecrawl's unique counts by 2 each. Each provider's own found and verified counts don't
  change.
- **S6 novelty.** The ground-truth builder read only tagged KB values, so a "verified" value in an empty cell could
  repeat something the KB already says untagged. Each of those 26 cells was checked against its company's KB leaf at
  933777c: the field's keywords were grepped and every hit was read (`grading/b/S6_nogt_kb_check.json`).
  - Six cells were already stated: five untagged, and one tagged that the builder missed (Browserbase's hosted MCP).
  - `novel_kb_new` leaves those six out. Per-provider new-fact counts fall by 6 each: Exa 36 → 30, Parallel 36 → 30,
    Firecrawl 30 → 24. The pre-registered `novel` count stays in `scored_b.json`.
- **Added reporting:**
  - "returned" as distinct entities by domain, alongside the raw count;
  - a paired exact sign test on the 133 ground-truth cells;
  - cost per right value;
  - claimed-field accuracy, counted only on entities one provider returned;
  - each provider's empty cells, by answer-key kind.

## Amendment 7 (post hoc: after the PR #25 Stage B review, 2026-09-26)

The adversarial review of the Stage B write-up (a PR #25 comment) found no scoring rule that favoured Exa. It did find
problems with framing, with a few verdicts, and with bookkeeping. This amendment logs every change. `scored_b.json`
keeps the as-judged figures next to each adjusted one.

- **One verdict changed.**
  - Diffbot's list price (Exa's value, `c19.list_price.v2`) goes from correction to `partly_correct`. The judge's own
    note calls the $0.02/1K a contact-sales floor, not a pay-as-you-go price, and the reviewer confirmed it.
  - Effect: Exa's strict right on ground truth goes from 106 to 105, and its new-fact count drops by 1.
- **One change declined.** The reviewer suggested `partly_correct` for Exa's Kagi free-tier value. Firecrawl's equally
  off-target value in the same cell stays `wrong` too, and changing only Exa's would be inconsistent.
- **Non-responsive values.** Four Firecrawl web-search-source values that the judges described as not answering the
  field are flagged in `S6_overrides.json`. They stay `wrong` in the pre-registered false-claim rate (10.2%). A second
  rate leaves them out (7.8%).
- **Correction types.** `S6_correction_types.json` types each of the 19 correction cells: 8 substantive, 4 detail,
  3 contested, 2 vendor claim only, and 2 where the vendor's pages conflict. The write-up counts only the substantive
  ones as KB errors.
- **Citation validity.**
  - It is now reported on values not judged wrong, because the brief defines `wrong` as unsupported by the cited page.
    Each comparison has a Fisher test.
  - Values judged wrong whose citation was marked valid are counted separately.
- **Tests.**
  - Company-level sign tests are added, because values cluster by run.
  - Value-level Fisher tests are added for completeness, false claims, and citation validity.
  - None is corrected for multiple comparisons, and the text says so.
- **S5 novelty rule.** The judges read the rule entity-level in S5b and fact-level in S5c. The primary rule is now
  fact-level (`S5_posthoc.json`):
  - Aisera (Exa), and Snowflake Cortex Agents and Grok Business (both Parallel), become new.
  - `_scratch/` research notes don't count as the KB.

  The entity-level rule is kept as a sensitivity, under which Apollo.io, Gamma, Jan, and Novita are known.

  | Rule | Exa | Parallel | Firecrawl | Any provider |
  |---|---|---|---|---|
  | Fact-level (primary) | 28 | 7 | 19 | 45 |
  | As judged | 27 | 5 | 19 | 42 |
  | Entity-level | 27 | 3 | 18 | 39 |
- **Exclusion flags.**
  - GitHub Copilot was flagged through a shared host (github.com), and is now unflagged.
  - ChatGPT Enterprise and Google LLC were missed, and are now flagged.
  - So Parallel returned 9 excluded entities, not 8.
  - `grade_prep_b.py --v2` skips shared hosts, matches names by lead word, and falls back to the evidence-URL domain
    for dedupe. The default still rebuilds the judged pools byte for byte.
- **Post-hoc KB check corrected.** Amendment 6 cited `competitive/browser-infrastructure.md:96` for Browserbase's MCP
  server, but that line sits under the Kernel heading. The cell is new, so each provider's new-fact count rises by 1.
  The three Crustdata cells are relabelled as absences.
- **Reporting.**
  - Firecrawl credits are shown as a range across plans, $0.00075–$0.005 a credit. The cap guard kept the maximum.
  - Firecrawl's latency is also shown per company.
  - S5 homepage-URL completeness is shown per provider (`S5_url_completeness.json`, derived from the raw outputs).
  - The input differences are disclosed: Exa's system prompts, and Parallel's all-required S6 schema.
  - The untested tiers are named for both providers.
- **Sanitization.**
  - The Stage B export is now a per-file allowlist.
  - A personal GitHub handle is redacted from a judge note.
  - The README says judge notes may quote short fragments.
- **Deletion timing.** Amendment 5 said local raw output is deleted "after grading". It will be deleted after this
  review's remediation; the owner runs the deletion.
- **Previously undocumented.**
  - The four S6 chunk pools the judges used were split from `S6_pool.json` by hand. They are exact subsets, and the
    review checked this.
  - The S6 strict/broad split was a reporting choice.
  - The answer key was recorded at e560164, whose leaves match 933777c. Neither its builder nor the exclusion-list
    builder was committed.
- **Changed files:** `score_b.py`, `make_tables_b.py`, `grade_prep_b.py` (the `--v2` option), and `export_data.py`.
  They postdate unblinding, so no hashes are recorded.

## Amendment 8 (Stage C: the big vendors' evidence, frozen 2026-09-27 before its first call)
- **Why.** The whole-market sizing (PR #36) could check its supply-side imputation only against small vendors
  (VER36-MJ-C). Four big lines have no public anchor in the KB: Google grounding ($100M in 2026), Anthropic's web search
  tool ($69.3M), OpenAI's ($60.0M) and Microsoft Grounding with Bing ($40.0M), $269M together. The owner asked
  (2026-09-27) to point the search tools at them and see which vendor does best.
- **Questions.**
  1. Which provider finds the most verified, new, decision-relevant evidence on these four lines, and at what cost?
  2. What does that evidence say about the lines? That goes to the sizing as a follow-up, outside this stage's scoring.
- **Set SV** (`code/sets/SV.json`): 24 questions, four vendors times six facets.
  - Tier 1 is the four facets that enter a line's arithmetic: volume, revenue, the denominator the sizing uses, and
    price.
  - Tier 2 is supply and named adoption.
  - Each question carries its search forms (the question text, three queries for Parallel, a keyword query for
    Firecrawl) and what the KB already knows.
- **Two arms**, every provider in each, one run per question:
  - **C1, search:** Stage A's adapters and parameters, unchanged. `code/stage_c/harness.py` is byte-identical to
    `code/harness.py` and reads its own config and ledger.
  - **C2, research:** each provider's research agent at its standard tier. Exa Agent at effort `medium` ($0.10 a run),
    Parallel Task on processor `pro` ($0.10), Tavily Research on model `mini` (metered; worst case 110 credits) and
    Firecrawl `/agent` with `maxCredits` 100. All four get the same prompt and the same output schema: a short answer,
    figures (value, unit, date, what it measures, source URL, source type, a short quote) and other facts, each with a
    URL (`code/stage_c/harness_research.py`).
- **Caps.**
  - C1: Exa $1, Tavily 100 credits, Parallel $0.50, Firecrawl 300 credits.
  - C2: Exa $3, Parallel $3, Tavily 800 credits, Firecrawl 2,400 credits.
  - A total guard of $28 covers both arms, with credits at list value ($0.008 for Tavily, $0.005 for Firecrawl).
  - A run the caps refuse is logged as skipped and scored as returning nothing.
  - Firecrawl's first five agent runs a day are free.
- **Order.** A one-question smoke test per arm first (question g4, whose answers are public prices), then the full
  set. Neither arm's results are read before both have run.
- **Judging, blind** (`judge-brief-c.md`).
  - Each question's pool holds every C1 result, deduplicated by URL, and every C2 figure and fact from all eight
    provider-arms. Provider names are removed, and the order is shuffled with a recorded seed.
  - Judges verify each claim on its source page. A C1 result earns credit only if its own page states the claim. A C2
    item earns credit only if its cited URL states it.
  - Verdicts: `new` (verified, relevant, not in the KB at the stage's base commit), `known` (verified and already in
    the KB), `unsupported` (the page doesn't state it), `false` (the page contradicts it), `unverifiable` (the page
    won't load) and `irrelevant`.
  - Each claim gets a tier (1 or 2), and each new claim a usefulness score (0–3, as in Stage A).
- **Metrics, fixed now:**
  - **Primary:** verified new tier-1 claims per provider-arm, and how many of them no other provider-arm found.
  - **Secondary:**
    - all verified new claims;
    - questions with at least one verified new claim;
    - C2's error rate: unsupported plus false, over its checkable items;
    - cost per verified new claim;
    - latency.
  - **The ranking.** The best provider in each arm is the one with the most verified new tier-1 claims, with cost per
    claim breaking ties. The combined ranking takes the union of a provider's two arms.
- **What's committed.**
  - The set, configs, code, the judge brief, and sanitized data: per-claim verdict records (the judge's own wording,
    URL, verdict, tier and provider-arms), costs and latencies.
  - No vendor text: runs stay local, as before.
  - Results are internal only, as the owner ruled for Stages A and B, since Parallel's terms restrict sharing
    benchmark results.
- **Frozen files** (SHA-256, first 16 characters):
  - `code/sets/SV.json` 00ca7b0449d6b38a;
  - `code/stage_c/harness.py` c267493cf7c69493;
  - `code/stage_c/harness_research.py` 9101f963df008294;
  - `code/stage_c/config.json` 828b00179a8e7800;
  - `code/stage_c/config_c2.json` f97b9059293693d7.
  - `judge-brief-c.md` 6f7245f5a2f0c580.

  Any later change goes in Amendment 9.

## Amendment 9 (Stage C smoke test, 2026-09-27, before the main run)
- **Tavily's schema form.** Tavily Research refused the shared output schema with a 400 error: only `properties` and
  `required` are allowed at the top level. The adapter now sends the same fields and descriptions in Tavily's form
  (`tavily_schema()`). g4's Tavily run is the second attempt; the first was refused before any work and cost nothing.
  The other three providers' requests are unchanged.
- **Firecrawl's free run.** Firecrawl billed its first agent run of the day (80 credits), although the harness
  expected it to be free. The ledger records what was billed, so the caps still hold, and every later Firecrawl run is
  treated as paid.
- **g4.** The smoke runs are g4's runs for scoring, in both arms and for all four providers. The main run covers the
  other 23 questions once each.
- **Changed file:** `code/stage_c/harness_research.py`, now bdf3785c6b2dde79. Nothing else changes: not the set, the prompt, the
  metrics, the caps or the judge brief.

## Amendment 10 (Stage C, after the research arm's main run, before any result was read)
- **Tavily's stage cap.** Tavily Research cost more a run than planned: 744 credits for 21 runs, about 35 each, against
  a worst case of 110. Its 800-credit stage cap refused its last four questions (o3, m2, m3, m6). The cap is a spend
  guard, not part of Tavily's tier, so it rises to 1,100 credits and those four run once each on the same settings
  (model `mini`). No provider gets a second run.
- **Firecrawl's five failures stand.** Five Firecrawl agent runs (g3, g6, o1, o6, m6) ended "Agent reached max
  credits" and returned nothing, at no charge. `maxCredits` 100 is part of Firecrawl's pre-registered tier, so they
  aren't retried with more budget, and they score as returning nothing.
- **Spend so far:** the search arm $1.23. The research arm: Exa $2.40, Parallel $2.40, Tavily 744 credits ($5.95) and
  Firecrawl 1,439 credits ($7.20). The total guard of $28 is unchanged.
- **Changed file:** `code/stage_c/config_c2.json`, now 9f7820edb44eba24.
- **Corrected in Amendment 12:** this amendment set aside Amendment 8's rule for refused runs, and its refusal record
  and credit arithmetic were wrong. The primary now follows Amendment 8.

## Amendment 11 (Stage C pooling and scoring code, committed before any verdict is read)
- **Pools.** `code/stage_c/grade_prep_c.py` (bb6e409337fc66da) built the 24 blind pools with seed 20260927: 771 unique result URLs
  and 1,370 research items, with neutral ids assigned after the shuffle. Eight judges (J1–J8) got three questions
  each, balanced by pool size, and the KB baseline is a checkout at 2a055dc.
- **Scoring.** `code/stage_c/score_c.py` (11ff04b2fdb1f1aa) implements Amendment 8's metrics: per provider-arm, verified new tier-1
  claims and the ones no other arm found; all new claims; questions with a new claim; the research arm's error rate;
  cost per new claim; median latency. It also scores each provider across both arms. It runs only after every judge
  has finished.

## Amendment 12 (post hoc: after the PR #38 review of Stage C, 2026-09-27)

The adversarial review of the Stage C results (a PR #38 comment) found no problem with the frozen files, the push
order or the arithmetic. It found counting errors that mostly credited the leader, a scoring rule set aside, and
post-hoc tests presented as if pre-registered. This amendment logs every change. It's post hoc: it was written after
the results and the review. Nothing was re-run or re-judged. `scores.json` keeps the as-judged figures, and the
corrected ones are in `scores_corrected.json`.

- **Corrected scoring.** A claim-level correction layer, `data/stage-c/corrections.json`, lists one entry per corrected
  claim with its reason and evidence. `score_c.py` applies it.
  - **Novelty.** The primary counts the KB checkout at 2a055dc as known, `_scratch/` included, in every question. That
    was the results page's stated rule, which the judges applied unevenly, and it reverses Stage B's (Amendment 7).
    - Tier 1, found by checking every new tier-1 claim against the baseline: `o4#6`, `o4#7` and `o1#10` (the red
      team's price check), and `o4#8` (its use-case note, which has both deep research models' prices).
    - Tier 2: `g1#3`, `m3#23`, `a1#6` and `a2#23`.
    - `g1#2` stays new: it adds Alphabet's 120,000-enterprise figure, which the KB lacks.
  - **Tiers.**
    - `o5#1`–`#4` are supply shares, tier 2 (Amendment 8's "Tier 2 is supply").
    - g3 and m3 aren't "the denominator the sizing uses": SV's own `kb_context` says they're not used by the sizing.
      Their tier-1 claims are tier 2 in the primary, except two known prices.
  - **Duplicates.**
    - A fact written in two or more questions counts once per provider-arm: 40 facts over 82 claims, including the
      review's four pairs.
    - Seven claims that only restate claims written separately (bundles) aren't counted again.
    - The rule, the screens that found the pairs, and their limits are in `corrections.json`.
    - `a2#22` is `a3#16`'s statistic and takes its tier 2, which resolves the review's tier conflict.
  - **Tavily's refused runs.** The primary follows Amendment 8: "A run the caps refuse is logged as skipped and scored
    as returning nothing." Tavily research's items on o3, m2, m3 and m6 are out, with their error-rate verdicts and
    131 credits.
  - **Sensitivities:**
    - Stage B's novelty rule, applied mechanically from `kb_file`;
    - g3 and m3 at tier 1;
    - Amendment 10's scoring;
    - the last two together.
  - **Corrected primary, new tier-1 claims.**
    - Search: Exa 11, Tavily 11, Parallel 8, Firecrawl 7.
    - Research: Exa 11, Parallel 10, Firecrawl 5, Tavily 4.
    - Both arms: Exa 16, Parallel 14, Tavily 13, Firecrawl 9. Found by no other provider: 6, 4, 4, 0.
    - In the search arm Exa ranks first only on the pre-registered tie-break (cost per claim).
- **Amendment 10's record, corrected.**
  - **It set aside Amendment 8's rule** that a run the caps refuse scores as returning nothing, without saying so. Its
    reason was defensible: the cap was a spend guard, not part of Tavily's tier; Tavily's request never changed; and
    no provider got a second run. But it helped Tavily against Firecrawl, whose five budget failures stand under its
    pre-registered tier. The primary now follows Amendment 8, and Amendment 10's scoring is a sensitivity.
  - **Only o3 was logged `skipped_cap`.** m2, m3 and m6 were waiting under the guard (`guard_wait_minutes` 20) when
    the run was stopped, and have no record before their re-runs.
  - **744 credits were billed over 20 runs** (g4 and 19 main-run questions), 37.2 each, below the 110-credit worst
    case. The first g4 attempt was refused at validation, cost nothing, and isn't a billed run.
  - **The cap was too low, not the runs too costly.** 800 credits can't cover the worst case (24 × 110 = 2,640) or
    even the observed mean (about 890).
- **Tests and columns, post hoc.**
  - The sign tests, the bootstraps, the leader-against-each comparisons and the "useful" column weren't in Amendment
    8's metrics. `make_tables_c.py`, which computed them, first appeared in the results commit and wasn't hashed.
  - The six comparisons are now Holm-adjusted, and the page says so; Stage B's rule was that the text notes an
    uncorrected test (Amendment 7).
  - As judged, the smallest adjusted p is 0.129 (unadjusted 0.021). None of the corrected primary's six is
    significant, even unadjusted.
  - The results page's "all logged before any result was read" now covers Amendments 9–11 only.
- **Costs.**
  - Tavily's and Firecrawl's credits are shown as ranges across plans, as Stage B did (Amendment 7): Tavily
    $0.005–$0.008 a credit, Firecrawl $0.00075–$0.005, from the KB's own price check.
  - Parallel's dollars are list-price estimates; Exa's are billed.
- **Records.**
  - LinkedIn and X paths in `claims.json` are hashed with `export_data.py`'s rule, and `score_c.py` refuses to write
    an unhashed social URL.
  - The sanitized judged files, the attribution maps and the judge-to-question assignment are committed.
    `runs_meta.json` gains each run's time.
  - Blinding relied on instructions: the attribution maps sat in `grading/C/_attrib/`, inside the judges' tree,
    although Stage B's write-up said to move them out. Next time, write attribution outside it.
- **Judge brief hash.** Amendment 8 first recorded `judge-brief-c.md` as 9bd7a85b2e03f9e5. Twenty-one seconds later,
  commit 9cff4f0 added a See Also section (kb_lint) and re-recorded the hash in place as 6f7245f5a2f0c580. That was
  before any call and before Amendment 9, which should have recorded it. The change is cosmetic.
- **SV's two copies.** `code/sets/SV.json` (hashed in Amendment 8) and `code/stage_c/sets/SV.json` (which the code
  reads) are identical: both 00ca7b0449d6b38a.
- **Before any Stage D.** SV marks g3 and m3 tier 1 although their `kb_context` says they're not used by the sizing.
  Fix SV's tier field, or tier 1's definition, including which year a figure must enter, before any Stage D.
- **Files** (SHA-256, first 16 characters), all written after unblinding:
  - `code/stage_c/make_tables_c.py`: b4e302a2e80cc073 at the results commit (c5f3c9c, not hashed until now); now
    bc74868f0bc6193c;
  - `code/stage_c/score_c.py`: 11ff04b2fdb1f1aa (Amendment 11); now 5f5681a87b75982b;
  - `code/export_data.py`: d7bf870630a26dbc before; now b207865af7709e6e (its redaction helpers are importable; the
    Stage A and B export is unchanged);
  - `data/stage-c/corrections.json`: 08512c86dd2226da.

## See Also
- [Experiment overview](overview.md)
- Experiments hub (KB page `experiments/README`, not published)
- [Stage A results](results-stage-a.md)
