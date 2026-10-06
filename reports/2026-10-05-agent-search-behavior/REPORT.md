# How agents used search: query style, fetch-from-memory and primary-source retrieval (2026-10-05)

This report analyses the stored transcripts of the two published batteries, the [web search
bakeoff](../2026-09-29-web-search-bakeoff/REPORT.md) and the [search gap
bench](../2026-10-03-search-gap-bench/REPORT.md). No agent was rerun. It asks how agents
used the search tools they were given, rather than which provider scored best. Every number
below comes from `scripts/analyze_search_behavior.py`, whose classification rules are listed
in [methodology](methodology.md). The analysis covers 974 search queries from the GAP
battery (both harnesses) and 655 from the bakeoff (Claude Code).

## Headline

- **Agents write keyword queries whatever the engine asks for.** Of 974 GAP search queries,
  7.3% read as natural language. In the bakeoff the figure is 6.0%. Exa's tool asks for a
  description of the ideal page rather than keywords; its queries were natural language in
  3% (Claude Code) and 2% (codex) of GAP calls. Because the automatic rule can miss
  descriptive phrasing, all 111 Exa queries from both batteries were also read by hand
  ([appendix](#appendix-every-exa-query)): none is phrased as a description of a page. The
  closest are a headline-like phrase used three times ("Python 4.0 release date announced by
  Python Steering Council") and a page title.
- **The two harnesses express constraints differently.** On the six provider arms, codex put
  `site:` into 31% to 67% of its queries, even where a structured domain filter existed.
  Claude Code wrote `site:` only on Firecrawl (6%) and otherwise used structured domain
  filters where a tool offered one (Perplexity `search_domain_filter`, Tavily
  `include_domains`, WebSearch `allowed_domains`).
- **Agents often fetch remembered URLs instead of searching.** In the bakeoff, Claude Code
  fetched pages without any search in 36 of 55 Exa cells, 35 of 55 Firecrawl cells, 34 of 54
  Tavily cells and 30 of 55 Parallel cells. Those provider arms therefore measured page
  fetching as much as search. On post-cutoff GAP tasks the same habit failed: Claude Code's
  Exa cells that only fetched passed 1/3, against 14/18 for its Exa cells that searched.
- **Retrieving the primary source mattered most on Claude Code.** Pooled over provider and
  native arms, Claude Code passed 94/111 cells (85%) when the task's primary source appeared
  in search results and 20/33 (61%) when it did not. Codex surfaced the primary source in
  most searched cells, so the comparison is thin there (95/116 against 8/10). Two arms
  passed without the primary page: Parallel (6/7) and Firecrawl (9/9) on Claude Code, which
  indicates their results carried the facts from other pages.

These are observations about agent behavior with each vendor's MCP server at its pinned
version and default settings. They are not measurements of the vendors' APIs.

## What each search tool asks for

Descriptions are quoted from the pinned MCP server packages and, for Parallel's hosted
server, from its published documentation.
| Arm | Search interface the agent saw | What the tool tells the agent about queries |
| Exa | `web_search_exa(query, numResults)` | "describe the ideal page, not keywords"; `query` is a "semantically rich description of the ideal page, not just keywords" |
| Parallel | `web_search(objective, search_queries, …)` | a natural-language `objective` plus "concise, related keyword queries of 3–6 words each" |
| Perplexity | `perplexity_search(query, …)` plus `ask`, `research`, `reason` | "Supports recency filters, domain restrictions, and a lower-latency fast search mode" |
| Tavily | `tavily_search(query, search_depth, …)` | `query` is described only as "Search query" |
| Firecrawl | `firecrawl_search(query, …)` | "Query operators, domain filters, `categories` … are described on their parameters" |
| Brave | `brave_web_search(query, …)` | lists when to use the tool; no guidance on query phrasing |
| native (Claude Code) | `WebSearch(query, allowed_domains, …)` | harness built-in |
| native (codex) | built-in web search | harness built-in; no parameters exposed to the transcript |
## Query style (GAP battery)

Parallel's `search_queries` are keyword queries by design; its natural-language part is the
separate `objective` field, which is counted as a structured parameter and not as a query.
Perplexity's user messages count as queries alongside its `query` inputs; system and assistant
messages are excluded. Codex native search reports its query lists in the transcript; page
views are counted as fetches.
| Harness | Arm | Queries | Natural language | Contains a year | Quoted phrase | `site:` | Median words | Structured parameters passed (calls) |
| claude-code | native | 101 | 7% | 43% | 39% | 0% | 11 | `allowed_domains` ×11 |
| claude-code | Brave | 72 | 17% | 44% | 18% | 0% | 8 | `count` ×23, `extra_snippets` ×21, `maximum_number_of_tokens` ×18, `freshness` ×1, `maximum_number_of_urls` ×1 |
| claude-code | Tavily | 38 | 13% | 45% | 18% | 0% | 9 | `max_results` ×38, `include_domains` ×19, `search_depth` ×17, `include_raw_content` ×1, `start_date` ×1 |
| claude-code | Exa | 38 | 3% | 58% | 0% | 0% | 10 | `numResults` ×38 |
| claude-code | Parallel | 94 | 5% | 24% | 2% | 0% | 6 | `model_name` ×27, `objective` ×27, `session_id` ×27 |
| claude-code | Firecrawl | 34 | 0% | 59% | 9% | 6% | 7 | `limit` ×34, `sources` ×34, `includeDomains` ×3 |
| claude-code | Perplexity | 70 | 20% | 36% | 10% | 0% | 9 | `max_results` ×60, `search_domain_filter` ×42, `max_tokens_per_page` ×5, `messages` ×5, `search_context_size` ×3, `search_recency_filter` ×2, `search_type` ×1 |
| codex | native | 48 | 0% | 50% | 33% | 10% | 8 | none |
| codex | Brave | 114 | 2% | 52% | 26% | 46% | 8 | `maximum_number_of_tokens` ×30, `count` ×15, `maximum_number_of_urls` ×13, `extra_snippets` ×7, `url` ×3 |
| codex | Tavily | 130 | 6% | 50% | 59% | 67% | 8 | `max_results` ×123, `search_depth` ×26, `include_raw_content` ×25, `end_date` ×3, `include_domains` ×3 |
| codex | Exa | 52 | 2% | 60% | 35% | 58% | 10 | `numResults` ×10 |
| codex | Parallel | 62 | 6% | 39% | 27% | 48% | 7 | `objective` ×28, `session_id` ×10, `max_results` ×3, `max_chars_per_result` ×1 |
| codex | Firecrawl | 42 | 2% | 74% | 26% | 31% | 8 | `limit` ×33, `sources` ×10 |
| codex | Perplexity | 79 | 14% | 71% | 33% | 42% | 10 | `max_results` ×20, `messages` ×18, `max_tokens_per_page` ×16 |
## Query style (web search bakeoff, Claude Code)
| Arm | Cells | Queries | Natural language | Contains a year | Quoted phrase | `site:` | Median words | Structured parameters passed (calls) |
| native | 54 | 137 | 2% | 34% | 9% | 4% | 7 | `allowed_domains` ×36 |
| Brave | 54 | 199 | 4% | 7% | 11% | 6% | 6 | `count` ×85, `maximum_number_of_tokens` ×80, `freshness` ×17, `goggles` ×16, `extra_snippets` ×11, `maximum_number_of_urls` ×9, `maximum_number_of_tokens_per_url` ×3 |
| Tavily | 54 | 25 | 0% | 32% | 12% | 0% | 10 | `max_results` ×18, `include_domains` ×10, `search_depth` ×4, `time_range` ×2, `start_date` ×1 |
| Exa | 55 | 21 | 0% | 19% | 5% | 0% | 10 | `numResults` ×21 |
| Parallel | 55 | 77 | 0% | 20% | 0% | 1% | 5 | `model_name` ×22, `objective` ×22, `session_id` ×22 |
| Firecrawl | 55 | 20 | 0% | 25% | 15% | 5% | 7 | `limit` ×20, `sources` ×20 |
| Perplexity | 55 | 176 | 16% | 13% | 10% | 0% | 8 | `max_results` ×154, `search_domain_filter` ×115, `max_tokens_per_page` ×21, `search_type` ×21, `messages` ×13, `search_context_size` ×13, `search_recency_filter` ×5 |
## Searching versus fetching from memory (web search bakeoff, Claude Code)

A cell is "fetched without searching" when it made at least one fetch call and no search
call. Brave's and Perplexity's servers expose no page-fetch tool, so their agents had to
search or answer from memory. Every arm has a handful of cells with no provider call:
answers given from memory and cells that ended before any tool call.
| Arm | Cells | Fetch tool in the arm | Fetched without searching | No provider call | Calls refused by the harness |
| native | 54 | WebFetch (mostly refused; see below) | 12 | 6 | 110 |
| Brave | 54 | no | 0 | 7 | 0 |
| Tavily | 54 | yes (`tavily_extract`) | 34 | 6 | 0 |
| Exa | 55 | yes (`web_fetch_exa`) | 36 | 6 | 0 |
| Parallel | 55 | yes (`web_fetch`) | 30 | 6 | 0 |
| Firecrawl | 55 | yes (`firecrawl_scrape`) | 35 | 6 | 0 |
| Perplexity | 55 | no | 0 | 7 | 0 |
On the bakeoff's stable-knowledge tasks, fetching a remembered URL is efficient: those tasks
passed in every arm, including no-search. It means the bakeoff's provider arms with a fetch
tool mostly measured that fetch path. The GAP battery, whose answers post-date the models,
shows the cost of the same habit on new information.

## Harness refusals: the Claude Code native arm

Claude Code's native arm exposed WebSearch and WebFetch through `--tools` but pre-approved
only WebSearch through `--allowedTools`. Headless Claude Code refuses a tool that is exposed
but not pre-approved ("Claude requested permissions to use WebFetch, but you haven't granted
it yet"). In the GAP battery every native WebFetch call was refused
(50 calls in 21 of 21 cells); in the bakeoff, 110 calls in 40 of 54 cells. No
provider arm and no codex arm had a refused call. The Claude Code native results in both
batteries therefore understate that harness's web tools. The arm configuration is fixed and
covered by a regression test. The fetch counts in this report include refused attempts. The arm was rerun with fetching
working; see [Native rerun (2026-10-06)](#native-rerun-2026-10-06).

## Primary-source retrieval (GAP battery)

Each GAP brief has one primary-source URL (the catalog oracle). A cell surfaced it when that
URL appeared among the URLs of returned search results, excluding search inputs. Unknown
coverage is excluded from both pass-rate groups. Recomputing against returned evidence
leaves the historical source counts unchanged; no searched cell has unknown coverage.
Facts can also come from secondary pages, so surfacing is a proxy for retrieval, not a
requirement for passing.
| Harness | Arm | Cells that searched | Unknown coverage | Primary source surfaced | Pass when surfaced | Pass when not surfaced |
| claude-code | native | 21 | 0 | 18/21 | 13/18 | 0/3 |
| claude-code | Brave | 21 | 0 | 18/21 | 15/18 | 2/3 |
| claude-code | Tavily | 21 | 0 | 16/21 | 13/16 | 2/5 |
| claude-code | Exa | 18 | 0 | 14/18 | 13/14 | 1/4 |
| claude-code | Parallel | 21 | 0 | 14/21 | 14/14 | 6/7 |
| claude-code | Firecrawl | 21 | 0 | 12/21 | 10/12 | 9/9 |
| claude-code | Perplexity | 21 | 0 | 19/21 | 16/19 | 0/2 |
| codex | native | 18 | 0 | 17/18 | 13/17 | 1/1 |
| codex | Brave | 18 | 0 | 16/18 | 15/16 | 2/2 |
| codex | Tavily | 18 | 0 | 16/18 | 14/16 | 2/2 |
| codex | Exa | 18 | 0 | 17/18 | 14/17 | 0/1 |
| codex | Parallel | 18 | 0 | 17/18 | 12/17 | 1/1 |
| codex | Firecrawl | 18 | 0 | 15/18 | 14/15 | 2/3 |
| codex | Perplexity | 18 | 0 | 18/18 | 13/18 | — |

## Native rerun (2026-10-06)

The Claude Code native arm was rerun on both batteries with WebFetch pre-approved: the 21 GAP cells
and all 54 bakeoff cells, on the same tasks, repetitions and model. No call was refused. The tables
above keep the original cells, because they document the refusal; the GAP and bakeoff reports use
the rerun for their native rows.

With page fetching available, the agent searched far less and read pages instead. On GAP it
issued 45 queries instead of 101, and it fetched 126 pages. On the bakeoff it issued 19 queries
instead of 137, and 36 of its 54 cells fetched remembered pages without searching at all.

| Battery | Cells | Queries (original) | Fetch calls | Refused calls (original) | Fetched without searching | Primary source surfaced | Pass when surfaced | Pass when not surfaced |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GAP | 21 | 45 (101) | 126 | 0 (50) | 0 | 17/21 | 15/17 | 1/4 |
| Bakeoff | 54 | 19 (137) | 426 | 0 (110) | 36 | — | — | — |

The rerun's GAP queries read as natural language in 2.2% of cases (original 7%), with a
median of 8 words (original 11). The rerun's run bundles are private, like the originals.

## Limits

- Cell counts per arm are small (18 to 21 in GAP, 54 to 55 in the bakeoff). Per-arm
  percentages move by about five points per cell.
- The natural-language rule is a heuristic, stated in [methodology](methodology.md); readers
  can rerun the script with their own rule.
- Each vendor was used through its MCP server at the pinned version and default settings.
  Other interfaces of the same vendor (direct API, other tools, other modes) were not
  tested.
- The two harnesses ran different task subsets in GAP (7 tasks for Claude Code, 6 for
  codex), so cross-harness differences mix agent behavior with task mix.
## Appendix: every Exa query

Every query sent to `web_search_exa` in both batteries, in transcript order, so readers can
judge the phrasing directly. Other arms' queries are summarised above but not reproduced.
| Battery | Harness | Query |
| GAP | claude-code | Copilot Billing Preview app deprecated usage report |
| GAP | claude-code | GitHub Copilot premium request budgets per user cap and usage report CSV export docs |
| GAP | claude-code | GitHub docs AI usage page billing settings group filter export AI credits Copilot limitations |
| GAP | claude-code | GitHub Copilot Billing Preview app deprecated usage-based billing budgets |
| GAP | claude-code | GitHub docs Copilot premium requests usage report export CSV billing 2026 |
| GAP | claude-code | GitHub docs AI usage page billing settings group filter export AI credits usage report limitations |
| GAP | claude-code | GitHub Copilot Billing Preview app deprecated usage report |
| GAP | claude-code | GitHub docs set budget for individual Copilot premium requests user-level budget |
| GAP | claude-code | GitHub docs AI usage page billing settings group filter export AI credits Copilot usage report limitations |
| GAP | claude-code | github.blog changelog 2026-08-04 Retiring the Copilot Billing Preview app |
| GAP | claude-code | GitHub Classroom deprecation sunset announcement 2026 |
| GAP | claude-code | Classroom 50 CS50 foundation GitHub Classroom alternative import autograding LTI |
| GAP | claude-code | Codio GitHub Classroom migration partner |
| GAP | claude-code | GitHub Classroom sunset deprecation announcement 2026 |
| GAP | claude-code | Classroom 50 CS50 open source GitHub Classroom replacement import migration |
| GAP | claude-code | GitHub Classroom deprecation sunset announcement 2026 |
| GAP | claude-code | GitHub Models deprecation retirement changelog 2026 BYOK inference |
| GAP | claude-code | GitHub Models deprecation retirement changelog 2026 BYOK paid usage |
| GAP | claude-code | Azure AI Inference beta SDK deprecated retirement August 26 2026 migrate to OpenAI v1 API |
| GAP | claude-code | GitHub Models deprecation retirement changelog 2026 BYOK |
| GAP | claude-code | GitHub secret scanning npm token revocation credential types 2026 changelog |
| GAP | claude-code | GitHub changelog npm granular access tokens publish and manage package access 2026 |
| GAP | claude-code | GitHub secret scanning npm token revocation credential types in scope 2026 |
| GAP | claude-code | GitHub changelog npm granular access tokens bypass 2FA publish manage package access 2026 |
| GAP | claude-code | GitHub changelog npm granular access tokens bypass 2FA publish package access management 2026 |
| GAP | claude-code | GitHub secret scanning npm token revocation credential types 2026 changelog |
| GAP | claude-code | GitHub changelog npm staged publishing generally available npm stage publish 2026 |
| GAP | claude-code | Python 3.10 security release tarfile extraction filter bypass CVE-2025-4517 |
| GAP | claude-code | CPython tarfile data filter vulnerability 2026 CVE security-announce |
| GAP | claude-code | Python 3.10 security release tarfile extraction filter bypass fix |
| GAP | claude-code | CVE-2026-4360 tarfile extract filter hardlinks Python fixed version |
| GAP | claude-code | GitHub docs converting a user into an organization warning cannot sign in personal account |
| GAP | claude-code | GitHub Enterprise Server docs promoting or demoting site administrator converting user to organization |
| GAP | claude-code | python.org release metadata API authentication vulnerability security advisory 2026 |
| GAP | claude-code | python.org release metadata vulnerability unauthenticated API modify release files 2026 |
| GAP | claude-code | python.org release API authentication bypass DEVCORE Splitline CVE advisory exploitation |
| GAP | claude-code | python.org release metadata vulnerability unauthenticated modify release files advisory 2026 |
| GAP | claude-code | Python Software Foundation security incident python.org downloads release API authentication |
| GAP | codex | GitHub Classroom sunset July 2026 September 2026 partner repositories |
| GAP | codex | GitHub Classroom retirement September 1 2026 repositories partner |
| GAP | codex | site:github.com/orgs/community/discussions/205975 "September 4" |
| GAP | codex | GitHub Classroom sunset September 2026 repositories partner retirement |
| GAP | codex | site:github.com/orgs/community/discussions GitHub Classroom "September 4" "2026" |
| GAP | codex | GitHub Models inference BYOK retirement July August 2026 paid existing usage |
| GAP | codex | GitHub Models retiring July August 2026 paid inference BYOK July 10 |
| GAP | codex | GitHub Models retirement July August 2026 paid inference BYOK existing usage |
| GAP | codex | site:docs.github.com converting user into organization January 2026 transfer repositories personal account enterprise server |
| GAP | codex | site:github.blog/changelog 2026 January converting user organization disabled January 2026 |
| GAP | codex | site:docs.github.com converting a user into an organization January 2026 transfer repositories |
| GAP | codex | site:github.blog/changelog 2026 January converting personal accounts organizations January 20 |
| GAP | codex | site:docs.github.com converting user into organization January 2026 transfer repositories personal account GHES |
| GAP | codex | site:github.blog/changelog 2026 January 20 convert account organization transfer personal account |
| GAP | codex | GitHub Copilot Billing Preview app retired individual budgets usage export billing reports |
| GAP | codex | site:docs.github.com Copilot AI usage page reports limitations billing API user level budgets export usage credits |
| GAP | codex | site:docs.github.com "AI usage" "report" "does not" billing usage reports |
| GAP | codex | site:docs.github.com "Setting up budgets" "user-level" "Budgets and alerts" |
| GAP | codex | "Copilot Billing Preview" budgets export usage |
| GAP | codex | site:docs.github.com AI usage user-level budgets billing reports Copilot AI credits |
| GAP | codex | site:docs.github.com "AI usage" "does not" reports billing |
| GAP | codex | site:docs.github.com "Viewing your usage" "AI" "report" billing |
| GAP | codex | site:docs.github.com "Automating usage reporting" "AI" |
| GAP | codex | site:docs.github.com Copilot usage metrics billing data coverage IDE chat github CLI |
| GAP | codex | site:docs.github.com "Setting" "user-level budgets" "Budgets" |
| GAP | codex | Copilot Billing Preview app individual budgets export raw usage finance billing dashboard |
| GAP | codex | site.github.blog/changelog 2026-08-04 retiring copilot billing preview |
| GAP | codex | site:docs.github.com AI usage page billing reports user-level budgets coverage limitations Copilot |
| GAP | codex | site:docs.github.com "AI usage" "does not" reports billing |
| GAP | codex | site:docs.github.com "AI usage" "coverage" |
| GAP | codex | site:docs.github.com "Viewing your usage of metered products" "AI usage" |
| GAP | codex | site:github.blog/changelog "user-level budgets" 2026 |
| GAP | codex | site:docs.github.com copilot usage metrics "billing" "coverage" |
| GAP | codex | site:github.blog/changelog billing CSV usage reports API 2026 |
| GAP | codex | npm token security granular tokens package access management July 2026 August 2026 GitHub credentials |
| GAP | codex | site:github.blog/changelog/2026 staged publishing npm May 22 trusted publishing tokens approve |
| GAP | codex | site:github.blog/changelog/ "2026-05-22" "npm" |
| GAP | codex | npm token security granular tokens package access github August 2026 publishing tokens |
| GAP | codex | site:github.blog/changelog npm December 9 2025 classic tokens revoked staged publishing July 2026 |
| GAP | codex | npm token security August 2026 package access granular tokens GitHub credentials |
| GAP | codex | site:github.blog/changelog npm staged publishing 2026 July 2FA |
| GAP | codex | python.org release metadata vulnerability unsigned downloads API incident 2026 2025 |
| GAP | codex | site.python.org Sigstore verify Python releases identity PEP 761 |
| GAP | codex | site.blog.python.org/2026/06/mitigated-api-bypass "Timeline" "30" "June" |
| GAP | codex | "python/pythondotorg" "3014" "merged" "June" |
| GAP | codex | python.org release metadata vulnerability authentication 2026 2025 security incident |
| GAP | codex | site:blog.python.org/2026/06/mitigated-api-bypass "Remediations" "Timeline" |
| GAP | codex | site:github.com/python/pythondotorg/issues/3010 OR site:github.com/python/pythondotorg/issues/3011 |
| GAP | codex | python.org release metadata security incident authentication bypass before June 30 2026 Trail of Bits |
| GAP | codex | python.org release metadata vulnerability authentication 2026 2025 API incident |
| GAP | codex | site.python.org security incident release metadata 2026 2025 downloads authentication |
| GAP | codex | Python Windows code signing certificates revoked August 2025 update investigation June 2026 |
| bakeoff | claude-code | Python 4.0 release date announced by Python Steering Council |
| bakeoff | claude-code | webpack md4 OpenSSL 3 Node 17 hash wasm md4 fix output.hashFunction xxhash64 sokra comment |
| bakeoff | claude-code | grype offline air-gapped vulnerability database import GRYPE_DB_AUTO_UPDATE false |
| bakeoff | claude-code | cargo audit --no-fetch --db local advisory database path offline |
| bakeoff | claude-code | Dependency-Track air-gapped offline vulnerability mirror configuration NVD OSV feeds URL |
| bakeoff | claude-code | Python 4.0 release date announced by Python Steering Council |
| bakeoff | claude-code | Python 4.0 release date announced by Python Steering Council |
| bakeoff | claude-code | Node.js documentation --openssl-legacy-provider option "Enable OpenSSL 3.0 legacy provider" |
| bakeoff | claude-code | aws.amazon.com data transfer out to internet US East (N. Virginia) first 10 TB per GB price same availability zone free |
| bakeoff | claude-code | grype air-gapped offline database import GRYPE_DB_AUTO_UPDATE false documentation |
| bakeoff | claude-code | Dependency-Track air-gapped offline NVD mirror configuration documentation |
| bakeoff | claude-code | Safety CLI 3 requires account login API key vulnerability database commercial license |
| bakeoff | claude-code | AWS EC2 on-demand pricing data transfer out to internet US East (N. Virginia) first 10 TB per month per GB; data transfer same availability zone free |
| bakeoff | claude-code | Stripe annual letter 2025 total payment volume private company revenue |
| bakeoff | claude-code | Stripe revenue estimate UK Companies House filing Stripe Payments Europe accounts net revenue |
| bakeoff | claude-code | Stripe annual letter 2025 total payment volume private company revenue |
| bakeoff | claude-code | Stripe revenue estimate net revenue report private company does not disclose financials |
| bakeoff | claude-code | Stripe Irish entity accounts filed Companies Registration Office revenue $5.1bn pre-tax profit 2024 |
| bakeoff | claude-code | Stripe annual letter 2025 total payment volume private company revenue estimate |
| bakeoff | claude-code | Grype offline air-gapped vulnerability database GRYPE_DB_AUTO_UPDATE false grype db import |
| bakeoff | claude-code | Dependency-Track air-gapped offline mirror NVD OSV internal mirror documentation |

Read the [summary data](summary.json), [calibration record](calibration.json), [methodology](methodology.md) and [reproduction steps](reproduce.md).
