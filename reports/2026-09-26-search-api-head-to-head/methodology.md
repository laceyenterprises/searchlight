# Search API head-to-head: methodology

This study calls search APIs directly; no coding agent is involved. Its design, query sets, parameters and metrics were
frozen in the [design](record/design.md) and the [pre-registration](record/preregistration.md) before the first paid
call, with SHA-256 hashes of the frozen files. Twelve amendments follow, each dated and marked as made before or after
results were seen. This page summarises the method; the record is authoritative.

## Providers and endpoints

Every provider's parameters come from its own best-practice documentation, read on 2026-09-26, and are written into
the harness configs (`code/config.json`, `code/config_b.json`, `code/stage_c/config.json` and `config_c2.json`). Every
provider got the same top 10 and ran on the same day.

- **Exa:** `/search` (`auto`, highlights) for S1 and S2; `/contents` with a live crawl for S3; the news category with a
  date filter for S4; the Agent API for S5 (capped) and S6 (with an output schema); find-similar for SP; Monitors for
  SM; and the Agent API at `medium` effort for Stage C's research arm.
- **Tavily:** search (`advanced`, chunks); extract for S3; news with a time range for S4; the Research API (`mini`) for
  Stage C's research arm. Tavily sat out S5 and S6: it sells no list-building or structured-extraction product, and
  search plus our own model would measure our model, not Tavily.
- **Parallel:** search (`advanced`, with an objective); extract for S3; `after_date` for S4; FindAll for S5; Task with
  an output schema for S6; Task (`pro`) for Stage C's research arm.
- **Firecrawl:** search plus a scrape of the top 3; scrape with JavaScript rendering for S3; `tbs` for S4; `/agent` for
  S5 and S6 (with a schema); `/agent` capped at 100 credits for Stage C's research arm.

## Sets

The query sets are drawn from the knowledge base's open questions and claims (see the [design](record/design.md)):
S1 40 known unknowns, S2 20 known knowns, S3 15 blocked pages, S4 10 companies' last two weeks, S5 three entity lists,
S6 20 companies by eight fields, SP 10 competitor homepages, SM 5 monitors and SV 24 questions on four grounding
vendors. Their hashes are pinned in `code/sets/SHA256SUMS` and match the prefixes frozen in the pre-registration.

## Grading

- **Pooled and blind.** Each question's results are pooled across providers, deduplicated and, from Amendment 4, shuffled
  with a recorded seed. Judges never see provider names; attribution is rejoined only after judging.
- **Verified on the page.** Judges are Claude agents working from written briefs ([first round](record/judge-brief.md),
  [re-grade](record/judge-brief-v2.md), [Stage B](record/judge-brief-b.md), [Stage C](record/judge-brief-c.md)). Every
  counted claim must be stated on its source page.
- **Strict per-claim credit** (Amendment 4): a provider is credited with a claim only when its own result page states
  it. Broad credit, where any pooled page may support the claim, is reported in the stage write-ups.
- **Novelty** is judged against the knowledge base at a recorded baseline commit. Claims are weighted for usefulness:
  3 corrects a knowledge-base claim, 2 is a new fact for a page, 1 is a new corroborating source and 0 is trivia.
- **Scripted sets.** S2 counts whether the cited primary source is in the top 10. S3 counts a page as recovered when the
  fetch returns at least 500 characters and a target keyword, with one recorded correction (a redirect).
- **S5 and S6.** An S5 entity counts when it meets every criterion of its list and is new to the knowledge base (a
  fact-level and a stricter entity-level rule are both reported). S6 cells are compared with the knowledge base's
  verified ground truth where it has one; the false-claim rate is the share of filled cells that are wrong.
- **Stage C.** Eight blind judges. A tier-1 claim enters a sizing line's arithmetic. The research error rate is the
  share of a research agent's checkable items whose cited page didn't state or contradicted them. Amendment 12 (post
  hoc) adds a claim-level correction layer (`data/stage-c/corrections.json`): novelty against the knowledge base's
  working notes, tiers, duplicates across questions, and scoring Tavily's four cap-refused runs as returning nothing, as
  pre-registered. Sign tests, Holm's adjustment and bootstraps were chosen after the results were known.
- **Monitors (SM).** One Claude agent, not blind (only Exa ran), checked each reported change against the vendor's
  current page and Internet Archive snapshots from before and after the run, and searched the knowledge base for it. A
  monitor passes when at least one change is confirmed, is a pricing or product change, and is dated inside the 30 days
  before the run.

## Gates, caps and stop rules

S1 ran on 20 questions first and would have stopped there had no provider resolved at least 3 that the built-in tools
couldn't. Stage B ran only for providers and workloads that showed signal in Stage A. Each provider had a hard cap,
and the owner set a $40 cap for Stages A and B together (Amendment 5); Stage C had its own caps (Amendment 8).

## Amendments that changed scoring after results were seen

- **Amendment 4:** after a review, Stage A was re-graded with shuffled pools and strict per-claim credit, and S2 was re-run
  on identical inputs.
- **Amendment 6:** Stage B scoring choices, written after unblinding.
- **Amendment 7:** fixes from the Stage B review, and the per-file allowlist for exported data.
- **Amendment 12:** the Stage C correction layer described above.

The record keeps the earlier figures beside the corrected ones in each case.

## Costs

Exa's costs are billed. Parallel's are list-price estimates from the harness config. Tavily's and Firecrawl's credits
are shown as ranges across their plans. Stage A and B costs value credits at the most they cost, as the cap guard did.

## Published data

`code/export_data.py` writes the data through an allowlist. It keeps request parameters, status, latency, cost, each
result's rank, URL, domain, published date and text length, crawl status codes, the pools (URLs only), attribution
maps, judge verdicts and scores. It never exports page titles, snippets, page text, extracted content, vendor-written
values or raw responses, and replaces the paths of social-media profile and post URLs with a stable hash. Judge notes
may quote short fragments of pages. The Monitors export (`code/monitors_harvest.py`) follows the same rule: change
types and fingerprints are kept, change summaries and titles only as lengths.

See [the report](REPORT.md), [summary data](summary.json) and [reproduction](reproduce.md). Calibration does not
apply: [calibration record](calibration.json).
