---
title: "Search-provider experiment: pre-registered design"
doc_type: experiment
delegation: full
influence_weight: low
staleness_window: 90d
kb_phase: build-out
canonical: false
status: active
source_last_updated: 2026-09-26
last_updated: 2026-09-26
last_verified:
token_estimate: 2325
confidence_score: 0.8
provenance_reviewed: false
tags: ["experiments", "search-provider-eval", "design"]
provenance_summary: >-
  The design the owner approved on 2026-09-26, frozen before the first API call. The body below is byte-identical to the pre-registered DESIGN.md.
---
> **Record note (Searchlight, 2026-10-06).** This file is the experiment's own record, copied from the private
> knowledge base where the experiment ran. Links between record files are converted to relative links; links to
> other knowledge-base pages, which are not published, are shown as plain text with the page's path. Nothing else is
> changed unless a further note says so. The Searchlight summary is [REPORT.md](../REPORT.md).

> [!note] Record copy
> - Everything from the H1 below to the See Also section is byte-identical to the pre-registered `DESIGN.md` (sha256 prefix `5f3aa89b59ad14d3`, the value recorded in [the pre-registration](preregistration.md)).
> - Two things changed after freezing, and neither touched a question, metric, or stop rule:
>   - The "Outputs" paths moved: code is now in `code/` and sanitized data in `data/`, both next to this file.
>   - Harness fixes were logged as amendments.

# Search-provider experiment design: what does Exa add to this KB, and how do Tavily, Parallel, and Firecrawl compare?

**Status:** draft for owner review (2026-09-26). No API calls have been made.

## Questions
1. **Net-new value.** How much verified, useful information does Exa add beyond what our native tools already put in the KB?
2. **Unique contribution.** On this workload (GTM, competitive, and market research), what does each of Exa, Tavily, Parallel, and Firecrawl surface that the others don't? Which capability produces it?

## Why this doesn't mean running the same jobs four times
1. **The KB is the baseline.** Round 1 checked 4,899 claims with native tools, so we measure marginal value: what a provider finds that the KB doesn't already have. Raw output volume doesn't count.
2. **A capability matrix, not a fourfold replication.** Only the cheap retrieval probes run on all four providers. The expensive workloads (entity lists, schema fill) run only on providers that sell that capability.
3. **Staged, with stop rules.** Stage A fits inside free tiers and must show signal before Stage B spends money.
4. **Pool, dedup, then verify once.** Results are pooled across providers and deduplicated by URL and by claim. Each unique claim is verified once and credited to every provider that surfaced it, so verification cost grows with unique findings, not with providers × results.
5. **Deterministic filters first, Claude last.** Scripts handle URL-novelty filtering (against every URL the KB already cites), recall checks, and blocked-page checks. Claude reads only novel material, blind to the provider.

## Arms
| Arm | What it is | Cost |
|---|---|---|
| Native baseline | Claude's web search tool, WebFetch, curl, and the built-in browser: everything behind the current KB. Anthropic lists Brave as its web-search subprocessor (secondhand), so this arm is effectively Brave plus agentic fetching. | Already spent |
| Exa | Search (`auto`, highlights), Contents (livecrawl), Agent, findSimilar, Monitors | Free tier covers Stage A |
| Tavily | Search (`advanced`, chunks), Extract, Research | 1,000 free credits a month |
| Parallel | Search (objective, `advanced`), Extract, Task, FindAll | Free requests; keyless Search MCP |
| Firecrawl | Search (+ scrape top 3), Scrape (JS rendering), `/agent` with a JSON schema | 1,000 free credits a month; 5 free agent runs a day |

Before any call, each provider's parameters are fixed from its own best-practice docs and written into the harness config.

## Query sets, pre-registered and drawn from the KB
| Set | n | What it tests | Source of the items |
|---|---|---|---|
| **S1 Known unknowns** | 40 | Can a provider answer what our exhaustive native research couldn't? | `open-questions.md` §C–E, the three needs-owner items, and "couldn't verify" notes in the round-1 ledgers. Only questions a public source could answer (e.g. "Which search provider powers Glean's web answers?", "Firecrawl's upstream search provider", "Sonar status after 2026-09-27"). |
| **S2 Known knowns** | 20 | Recall calibration: does the provider surface the primary source we already cite? | A stratified sample of KB claims (pricing, customers, legal, compliance) |
| **S3 Blocked sources** | 15 | Recovery of pages native fetch couldn't read | JavaScript trust centers (Tavily, Firecrawl, Anthropic, Cursor), `exa.ai/evals`, the Websets billing page (HTTP 429), Reddit threads, Product Hunt, PDFs whose text didn't extract |
| **S4 Freshness** | 10 | Net-new dated intel from the last 14 days | The 10 highest-importance competitors, one news query each |
| **S5 Entity lists** | 3 | Precision, plus entities new to our rosters | (a) SIs with agentic-AI practices that publish web-search integrations; (b) enterprise-search and workplace-AI products with public-web answers, and who powers them; (c) public pages that name Exa as a subprocessor or search provider (customer discovery) |
| **S6 Schema fill** | 1 table (20 competitors × 8 fields) | Dogfoods the new internal-plus-public JTBD: mold public data into our own schema | Fields: web-search provider, SOC 2, ZDR, BAA, MCP server, free tier, list price, last funding. Half the cells already have KB-verified ground truth. |

## Capability matrix (a blank cell means we don't run it)
| Workload | Exa | Tavily | Parallel | Firecrawl |
|---|---|---|---|---|
| S1–S2 search | `/search` auto + highlights | search advanced | search advanced + objective | search + scrape top 3 |
| S3 fetch | `/contents` livecrawl | extract | extract | scrape |
| S4 fresh | news category + date filter | topic news + time range | `after_date` | `tbs` |
| S5 lists | Agent `auto` (capped) | | FindAll | `/agent` |
| S6 schema | Agent `input.data` + `outputSchema` | | Task + output schema | `/agent` + schema |
| Exa-only probes | findSimilar on 10 competitor homepages (roster discovery); Monitors on 5 competitor changelog and pricing pages for 2 weeks (drift detection vs our staleness windows) | | | |

Tavily sits out S5 and S6 because it sells no list-building or structured-extraction product. Running search plus our own LLM would measure our LLM, not Tavily.

## Metrics
- **Primary:**
  - verified net-new facts per provider, weighted by usefulness (3 = corrects a KB claim; 2 = new fact for a leaf; 1 = new corroborating source; 0 = trivia);
  - verified facts unique to one provider;
  - cost per verified net-new fact.
- **Per set:**
  - S1: resolution rate.
  - S2: recall@10 of the primary source.
  - S3: recovery rate (the target fact is readable).
  - S4: dated items that are new to the KB.
  - S5: precision and number of novel entities.
  - S6: field accuracy × completeness, and citation validity.
- **Always:** false or unsupported claim rate (for synthesized outputs), latency, and the source mix (primary vs secondary).

## Grading pipeline
1. **Harness.** A Python script with one adapter per provider and hard budget counters. It writes raw JSONL with cost and latency, and runs every provider on the same day.
2. **Filter by script.** Normalize URLs. Drop URLs the KB already cites. Pool what's left across providers.
3. **Score S2 and S3 by script.** URL match for S2; page length plus target phrase for S3.
4. **Blind judging.** A Claude judge reads each query's pooled novel snippets without provider labels. It proposes candidate facts and a usefulness score.
5. **Verify.** Candidates scoring 2 or more are checked against the cited page with native fetch, once each.
6. **Unblind.** Attribute findings to providers, compute metrics, and write the results memo.

## Budget (list prices from the KB's pricing leaves; re-checked before running)
| Stage | Calls | API spend | Claude tokens |
|---|---|---|---|
| **A**: S1–S4, ~70 queries × 4 providers | ~280 searches + 60 fetches | about $1 at list prices. It fits free tiers: Exa ~$0.50 of its $20 in credits; Tavily ~150 of 1,000 credits; Parallel ~$0.40; Firecrawl ~370 of 1,000 credits | ~1.2M (judging ~70 query pools, verifying ≤150 candidates) |
| **B**: S5–S6 (only if A shows signal) | Exa Agent: 3 lists capped at $2 each, plus 1 schema run at `high` ($0.50) or 20 at `medium` ($2); Parallel FindAll plus 20 Task runs at `core` ($0.025) or `pro` ($0.10); Firecrawl `/agent` (free runs) plus JSON scrapes (5 credits a page) | ≤ $20 hard cap | ~0.8M |
| **Total** | | **≤ $25 cap** | **~2M** (round 1's fleet used ~12M) |

## Stop rules
- **S1 checkpoint.** S1 runs on 20 questions first. If no provider resolves at least 3 that native tools couldn't, stop S1 there and record "KB saturated for search at this budget". That is a finding in itself.
- **Stage B gate.** A provider and workload go to Stage B only if Stage A showed signal: at least 5 verified net-new facts, or at least 30% blocked-source recovery.
- **Hard caps.** A provider that hits its cap stops. There are no top-ups without owner approval.

## Bias controls
- **Pre-registration.** Queries, parameters, and metrics are frozen in this file before the first call.
- **Blind judging and same-day runs.** Everyone gets identical top-k (10) and best-practice parameters.
- **Workloads where Exa may be weak.** This is Exa's GTM KB, so some are included on purpose: freshness, and blocked pages Exa doesn't render.
- **Internal use only.** Results are workload-specific and small-n, and never become external benchmark claims. That's the same rule the KB applies to Exa-run evals.
- **Terms of service.** Each vendor's terms on benchmarking and publishing results get checked before anything leaves the team.

## Outputs
- **Run data.** `_scratch/runs/provider-eval/`: raw JSONL, costs, and the harness config.
- **Results memo.** `_scratch/review/provider-eval-results.md`: per-set results, overlap (Venn) of unique facts, cost per fact, and what each capability contributed.
- **KB updates.** One PR with the verified net-new facts, cited to their primary sources (not to the provider), through the usual review.

## Needed from the owner
1. **Go-ahead and budget.** Go or no-go, and the budget cap: proposed ≤ $25 in API spend and ~2M Claude tokens, Stage A first.
2. **API keys.** One key each for Exa, Tavily, Parallel, and Firecrawl; free-tier accounts are enough for Stage A.
   - Put them in a local env file that the harness reads, rather than in chat.
   - Set per-key spend caps where the provider supports them (Exa does).
   - A keyless smoke test is possible via Tavily's keyless REST and the Parallel, Exa, and Firecrawl MCP free tiers, but it's rate-limited and not a fair main run.
3. **Stage B scope.** Include S5 (entity lists) and S6 (schema fill)?

## See Also
- [Experiment overview](overview.md)
- Experiments hub (KB page `experiments/README`, not published)
- [Stage A results](results-stage-a.md)
