# Search API best practices for the unique-insight bake-off

Read on 2026-11-11. Every doc cited below was read on that day unless a row says otherwise. The frozen request
bodies are in `config/insight/profiles/<arm>.<operation>.json`, and the shared transforms and templates (query-writer, research
brief, extractor brief) are in `config/insight/profiles/_shared.json`. Any field we could not confirm from the docs carries an
`unverified_fields` entry in its profile.

**Workload.** Natural-language research questions of 154 to 328 characters and 19 to 44 words
(`catalogs/insight/software-factories-2026-11.json`). Most are multi-part. One (Q24) carries `freshness_days: 60`. Every
question fits every vendor's query limit verbatim. The tightest limits are Firecrawl (500 chars) and Brave LLM
Context (600 chars, 75 words).

**Two operations per vendor:**
- **search**: the vendor's retrieval, plus any page-fetch step its docs prescribe. Our fixed extractor model then
  reads the returned text.
- **research**: the vendor's own answer or research agent, given the shared research brief.

---

## 1. Exa

### Products and endpoints
| Product | Endpoint | Status 2026-11-11 |
|---|---|---|
| Search | `POST https://api.exa.ai/search` | Recommended types are `lite`, `auto` and `deep`. Also `deep-lite`. `fast`, `instant` and `deep-reasoning` are legacy. |
| Contents | `POST /contents` | Up to 100 URLs. Text, highlights and summary are each billed per page. |
| Answer | `POST /answer` | Models `exa`, `exa-pro`, `exa-research` and `exa-fast`. $5 per 1k. Not deprecated. |
| Agent | `POST /agent/runs`, `GET /agent/runs/{id}`, `/cancel`, `/stop`, `/events` | Efforts: `minimal`, `low`, `medium`, `high`, `xhigh`, `auto` (the default) and `ultra`. |
| findSimilar | `POST /findSimilar` | **Deprecated.** The spec says to "prefer `/search` with a query describing the source". |
| Research (`/research/v1`) | none | Not in the current docs index or OpenAPI. Agent and Deep Search replace it. |
| Websets, Monitors, Batches | `/v0/websets/*`, `/monitors`, `/batches` (beta) | Not relevant to this workload. |

### Recommended parameters
**search** (`exa.search.json`): `type: "auto"`, `numResults: 10` and `contents.highlights: {dynamic: true}`, sent with
the header `Exa-Beta: dynamic-highlights-2026-08-28`. A post-step then calls `/contents` with
`text.maxCharacters: 20000` on the top 3 results.
- `auto` is the type for "ranked pages from a well-formed query" when the application does its own reasoning.
  Use `deep` for synthesis or structured outputs, and `lite` only when cost or latency matters most. Exa's advice
  is to change the search mode last, because "a different mode can't fix an underspecified query".
  [Search Best Practices](https://exa.ai/docs/search/best-practices.md)
- Use Dynamic Highlights "when several results feed the same agent or context window"
  ([Highlights](https://exa.ai/docs/search/highlights.md)). It is also the configuration Exa submitted to the
  Artificial Analysis index ([Exa 3.0](https://exa.ai/blog/exa-3)). See section 1a.
- Search with highlights first, then call the Contents API for the URLs that need more context.
  [Search Best Practices](https://exa.ai/docs/search/best-practices.md)

**research** (`exa.research.json`): `POST /agent/runs` with `{query: RESEARCH_BRIEF, effort: "high"}`. There is no
`outputSchema`; read `output.text` plus `output.grounding`. Poll `GET /agent/runs/{id}` until the run is
`completed`, `failed` or `cancelled`, and call `/stop` on timeout, which keeps partial results.
- Treat `query` as a task specification: the entity, the scope, the evidence bar and the fields to return. "If a
  prose answer is enough, omit [outputSchema]." Tighten the query before raising effort.
  [Agent Best Practices](https://exa.ai/docs/agent/best-practices.md)
- `high` ($0.50) is the most thorough fixed-price effort below `xhigh` ($1.00). `auto` and `ultra` are metered at
  $0.10 per ACU plus $0.005 per search, with $5 and $20 caps, so they must carry a `budget.maxCostDollars`.
  [Agent quickstart](https://exa.ai/docs/agent/quickstart.md), [Pricing](https://exa.ai/pricing)

### Query writing
- Use natural language: "Describe the pages you want rather than listing keywords. Include the subject, source
  type, and time period." Put domain limits in `includeDomains`, never `site:`.
- `objective` (up to 4,096 chars) is optional on `/search`. It says "which documents should rank first, which
  should be excluded, and what specific facts or figures to pull". The primary profile omits it; variant EXA-S-OBJ
  adds the shared objective.
- `additionalQueries` (1 to 10) works only with the deep types, and Exa warns against "minor rephrasings".
  [Deep Search](https://exa.ai/docs/search/deep-search.md)

### Freshness and content controls
- **Recency:** use `startPublishedDate`/`endPublishedDate`. Don't use `maxAgeHours` for recency; it controls cache
  age (`0` = live, `-1` = cache only). `lite` rejects `maxAgeHours`. `livecrawl` is deprecated, so never send it
  together with `maxAgeHours`.
- **Content:** highlights are relevance-sized. On SimpleQA, 500 chars of highlights matched the accuracy of the
  first 8,000 chars of text. `text` is positional, reading from the top of the page (`maxCharacters` 1 to 1M;
  `verbosity` compact, standard or full). Use `/contents` for depth.

### Rate limits, pricing and pitfalls
- `/search` and `/answer` allow 10 QPS; the deep types 5 QPS; `/contents` 100 QPS. `/agent/runs` allows 5 QPS and
  50 active runs. Each POST counts as 2 requests, and polling is free.
  [Billing and Rate Limits](https://exa.ai/docs/admin/billing.md)
- Prices per 1k: lite $4; auto and fast $7; deep-lite and deep $12; deep-reasoning $15 (legacy). Each result
  beyond 10 adds $1. Contents cost $1 per 1k pages per content type. Answer costs $5.
- Per question at the chosen settings: about **$0.010** for search (up to $0.020 if highlights bill per page) and
  **$0.50** for research.
- Pitfalls: remove deprecated fields (`numSentences`, `highlightsPerUrl`, `livecrawl`, `startCrawlDate`,
  `includeText`, `useAutoprompt`). Log `warnings` but never pass them to a model; pass `notices` to the model.
  `resolvedSearchType` is deprecated and may be empty. `highlights.maxCharacters` is incompatible with
  `dynamic`. HTTP 402 means out of credits or over budget.

### What changed since 2026-09
See section 1a. In short: the type enum was reshuffled (`lite` is new; `fast`, `instant` and `deep-reasoning` are
legacy), Agent gained `ultra` and `/stop`, findSimilar was deprecated, Dynamic Highlights shipped, and responses
gained `warnings` and `notices`.

### 1a. What Exa 3.0 changes for the bench

**What 3.0 is.** Exa launched 3.0 on 2026-10-19 ([launch post](https://exa.ai/blog/exa-3); changelog entry of the same
day, ["Lite search, recommended search types, and warnings"](https://docs.exa.ai/changelog)). It brings:
- a larger embedding model ("more compute and data than any previous model"), applied to all search types;
- billions of new documents, with Exa claiming 2T+ URLs tracked and 100B pages served;
- **Exa Lite**, a new type at $4 per 1k on the same index as Auto ($7) with less compute per query;
- **Dynamic Highlights announced as GA "at no additional charge"**;
- recommended types `lite`/`auto`/`deep`, with `fast`/`instant`/`deep-reasoning` kept as legacy at their old prices
  (suggested moves: `instant` to `lite`, `fast` to `lite` or `auto`, `deep-reasoning` to `deep` or Exa Agent);
- new `warnings` and `notices` arrays on responses.

Exa's own benchmarks (vs. Perplexity, Brave and Parallel) are self-run, and the setup is not described.

**Does it change the arm structure?** No.
- 3.0 is a retrieval upgrade. It adds no new answer or research product.
- The research arm stays the Exa Agent, which predates 3.0 (June 2026), with `ultra` and `/stop` added on
  2026-09-24.
- Deep Search (`type: "deep"` with `systemPrompt`, `outputSchema` and `additionalQueries`) sits between search and
  research. We pre-register it as variant EXA-D, not a third arm. It returns `output.content` and `output.grounding`
  and is synchronous, at $12 per 1k.

**Does it change the search profile?** Yes, in two ways:
1. The content view moves from `highlights: true` to `highlights: {dynamic: true}`, with the beta header.
2. `auto` is now the 3.0-recommended default rather than one of six peers.

The 2026-09 harness used `type: auto` with `highlights: true`. That stays on file as variant EXA-S-STD.

**Docs conflict.** The 3.0 post says Dynamic Highlights is GA. The API reference read on 2026-11-11 still marks it
`x-exa-lifecycle: beta` and rejects `dynamic` without the `Exa-Beta: dynamic-highlights-2026-08-28` header. We send
the header. If Exa retires the header, a request could fail; the profile flags this.

**Does it change the cost model?**
- Auto is still $7 per 1k, and Dynamic Highlights carries "no additional charge".
- The post also says Auto's price covers "candidate generation, highlights, and ranking". That suggests highlights
  on `/search` may not bill per page, but the pricing page doesn't say so. The estimate is $0.010, with $0.020 as
  the upper bound.
- `price-table.yaml` needs `search:lite: 0.004`, and `instant`, `fast` and `deep-reasoning` relabelled as legacy.
- Agent pricing is unchanged by 3.0: fixed efforts plus metered `auto`/`ultra`.

**Does it change async polling?** Not because of 3.0. The Agent lifecycle is the same as in our Stage C harness
(`POST /agent/runs`, then poll `GET /agent/runs/{id}`). Since 2026-09-24 there is `POST /agent/runs/{id}/stop`,
which keeps partial results, and it should replace `/cancel` in the timeout path. `/search`, including `deep`,
stays synchronous (`stream: true` works only with `outputSchema`).

**Changes the Searchlight `ExaProvider` adapter needs** (`lib/python/sew/providers.py`, verified 2026-09-19):
1. **Type enum comment and peers.** `DEFAULT_SEARCH_TYPE = "auto"` is still right. The comment listing
   `auto | fast | instant | deep-lite | deep | deep-reasoning` should list `lite | auto | deep-lite | deep` plus the
   legacy types. The note "`fast` is the latency-matched peer of Parallel's `fast`" is stale: Exa now points `fast`
   users to `lite` or `auto`.
2. **`lite` is strict.** Lite accepts only `query`, `objective`, `userIdentifier`, `metadata`, `category`,
   `numResults`, `contents.highlights`, `contents.text`, `contents.snapshotAsOf`, `userLocation`, the domain
   filters, the date filters, `moderation` and `compliance`. It rejects anything else with a 400 and rejects
   `maxAgeHours`. `body.update(options)` can forward a stray option into a 400.
3. **Dynamic Highlights view.** `content_view` knows only `highlights` and `text`. It needs a `dynamic` view that
   sends `contents.highlights = {"dynamic": true}`, adds the `Exa-Beta` header (`_headers` currently returns only
   `x-api-key`), and refuses `max_chars_per_result`, since `maxCharacters` is incompatible with `dynamic`.
4. **`warnings` and `notices`.** These are new response arrays. Record `warnings` in call metadata and never pass
   them to the model. Pass `notices` to the extractor with the results; Exa asks for this, for example when a
   `site:` match was dropped.
5. **`resolvedSearchType` is deprecated** and "may be empty". `_cost_metadata` records it as a unit, so stop
   treating it as meaningful.
6. **`costDollars` shape.** It is `total` plus optional `search.{neural,keyword}`, `summary` and
   `contents.{text,highlights,summary}`, and "some search types report their cost only in `total`". The adapter
   copies the dict whole, which is fine, but any table must read `total`.
7. **Categories.** `publication` replaced `research paper` (2026-07-23). `pdf`, `github` and `tweet` are deprecated.
   The adapter already omits `github`; `pdf` and `tweet` should also be absent from any suite.
8. **findSimilar is deprecated.** The SP set (2026-09) used it. Re-encode SP as `/search` with a query that
   describes the source.
9. **Agent.** It is not in the adapter, only in `stage_c/harness_research.py`. Widen the effort enum to `minimal`,
   `low`, `medium`, `high`, `xhigh`, `auto` and `ultra`; add `budget.maxCostDollars` for `auto` and `ultra`; use
   `/stop` instead of `/cancel` on timeout.
10. **Auth.** Exa accepts both `x-api-key` and `Authorization: Bearer`. No change is needed.

---

## 2. Parallel

### Products and endpoints
| Product | Endpoint | Notes |
|---|---|---|
| Search | `POST https://api.parallel.ai/v1/search` | Modes: `turbo` (~200 ms), `fast` (~700 ms, added 2026-08-19), `basic` (~1 s), `advanced` (~3 s, the default). |
| Extract | `POST /v1/extract` | Up to 20 URLs. Excerpts by default; `advanced_settings.full_content`. |
| Task | `POST /v1/tasks/runs` (async) | Processors from `lite` to `ultra8x`; `output_schema` json, text or auto. |
| Responses | `POST /v1/responses` (OpenAI-compatible, model `parallel`) | `reasoning.effort` low, medium or high; 5 to 60 s; synchronous only. |
| FindAll | `POST /v1beta/findall/runs` | Entity lists. Not used for this workload. |
| Decisions | `POST /v1/decisions` (added 2026-10-26) | Closed-ended questions. Not used. |
| Chat | `POST /v1beta/chat/completions` | Superseded by Responses for new work. |

### Recommended parameters
**search** (`parallel.search.json`): `objective` plus 2 to 3 `search_queries`, `mode: "advanced"`, `client_model`,
`session_id` and `advanced_settings.max_results: 10`. A post-step calls `/v1/extract` on the top 3 with the same
objective, queries and `session_id`, and `advanced_settings.full_content: true`.
- `advanced` is for "multi-hop background agents that can tolerate extra latency", with deep research as an
  example. [Modes](https://docs.parallel.ai/search/modes.md)
- `client_model` lets Search size excerpts for the model that consumes them. Reuse one `session_id` across the
  Search and Extract calls for a task. [Search Best Practices](https://docs.parallel.ai/search/best-practices.md),
  [API reference](https://docs.parallel.ai/api-reference/search/search.md)

**research** (`parallel.research.json`): `processor: "ultra"` with `task_spec.output_schema: {"type": "auto"}`.
Poll the run, then fetch `/result`.
- `ultra` covers "multi-source deep research" in 3 to 25 minutes (p50 4 min, p90 10 min). `pro` covers
  "exploratory web research" in 2 to 10 minutes.
  [Processors](https://docs.parallel.ai/task-api/guides/choose-a-processor.md)
- The auto schema produces Deep Research-style output, with nested FieldBasis citations, only on `pro` and above.
  [Task Spec](https://docs.parallel.ai/task-api/guides/specify-a-task.md)
- The Responses API at `high` ($0.25) is the documented synchronous alternative, pre-registered as variant
  PAR-R-RESP.

### Query writing
- `objective`: a natural-language goal of up to 5,000 chars that includes context and source or freshness needs.
- `search_queries`: 1 to 5 keyword queries (2 to 3 recommended), each 3 to 6 words and at most 200 chars. Don't
  use sentences or `site:`, and put the key entity in every query.
- The top pitfall: "Don't send the raw question as both `objective` and the only search query."
- Our shared query-writer produces both inputs once per question (see `_shared.json`), so the vendor-prescribed
  rewrite is not hand-tuned.

### Freshness and content controls
- Recency: `advanced_settings.source_policy.after_date` (YYYY-MM-DD). `include_domains` and `exclude_domains`
  together allow at most 200 entries.
- Cache: `fetch_policy.max_age_seconds` (minimum 600), `timeout_seconds` and `disable_cache_fallback`. Live
  fetching "noticeably increases latency", so leave it unset.
- Content: excerpts are on by default. `max_chars_total` is top-level and sized dynamically by default.
  `excerpt_settings.max_chars_per_result` caps each result. Extract `full_content` returns the whole page as
  markdown.

### Rate limits, pricing and pitfalls
- Search and Extract allow 600 per minute each; Task runs 2,000 per minute; FindAll 300 per hour; Chat 300 per
  minute. GET requests are free. [Rate limits](https://docs.parallel.ai/getting-started/rate-limits.md)
- Prices per 1k: turbo and fast $1; basic and advanced $5 (10 results included, +$1 per 1k extra results).
  Extract $1 per 1k URLs. Task: lite $5, base $10, core $25, core2x $50, pro $100, ultra $300, ultra2x $600,
  ultra4x $1,200, ultra8x $2,400. Responses: low $10, medium $50, high $250.
  [Pricing](https://docs.parallel.ai/getting-started/pricing.md)
- Per question: **$0.008** for search and **$0.30** for research.
- Pitfalls:
  - `/v1` and `/v1beta` shapes don't mix, and mixing them returns a 422.
  - `max_results`, `source_policy` and `fetch_policy` must sit inside `advanced_settings`.
  - `search_queries` is required; sending only `objective` returns a 422.
  - Queries past the fifth are dropped with a warning.
  - `max_results` above 20 is clamped to 20.
  - `turbo` handles English and Japanese only.

### What changed since 2026-09
- `fast` mode was added (2026-08-19, $1 per 1k).
- `client_model`, `session_id` and a top-level `max_chars_total` are now documented, and the response gains
  `usage[]` SKU counts and `warnings`.
- The Responses API launched (2026-07-16) and gained domain filters and `data_sources` in September.
- Per-element basis reached GA (2026-08-21), and MCP 2.0 shipped (2026-11-05).
- Prices are unchanged versus `price-table.yaml`, which still lacks the Responses API row.
- Adapter: `ParallelWebProvider.DEFAULT_MODE = "fast"`, so the bake-off must pin `advanced`. The adapter sends
  neither `client_model` nor `session_id`.
- Stage C used `pro`; this bench uses `ultra`, with `pro` kept as a variant.

---

## 3. Tavily

### Products and endpoints
| Product | Endpoint | Notes |
|---|---|---|
| Search | `POST https://api.tavily.com/search` | Depths: `ultra-fast`, `fast`, `basic` (the default) and `advanced`. |
| Extract | `POST /extract` | 1 to 20 URLs; `basic` or `advanced`. |
| Research | `POST /research`, then `GET /research/{request_id}` | Models `mini`, `pro` and `auto` (the default). |
| Crawl and Map | `POST /crawl`, `POST /map` | Site-level. Not used. |

### Recommended parameters
**search**: `search_depth: "advanced"`, `chunks_per_source: 3`, `max_results: 10`, `topic: "general"`,
`include_answer: false`, `include_raw_content: false`, `auto_parameters: false`, `include_usage: true` and
`session_id`. A post-step calls `/extract` on the top 3 with `extract_depth: "advanced"` and `format: "markdown"`.
- `advanced` gives the highest relevance and fits "niche topics, very recent pages, multi-faceted questions, or
  long, detailed queries".
- `chunks_per_source` takes at most 3.
- "For thorough extraction, search first, then pass the URLs to the Extract API."
- `auto_parameters` "can silently raise your cost by selecting `advanced`".
- Sources: [Search Best Practices](https://docs.tavily.com/documentation/best-practices/best-practices-search),
  [Search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)

**research**: `model: "pro"`, `citation_format: "numbered"`, `output_length: "long"`, then poll.
- `pro` is "multi-agent research for complex, multi-domain topics" and suits "maximum accuracy".
  [Research Best Practices](https://docs.tavily.com/documentation/best-practices/best-practices-research)
- Stage C used `mini`, kept as variant TAV-R-MINI.

### Query writing
- Keep queries **under 1,500 chars** and write them as short search queries, not prompts.
- For multi-topic questions, send separate focused searches. Ours are multi-facet questions about one subject,
  which the docs map to `advanced`. Splitting into 3 sub-queries is pre-registered as variant TAV-S-SPLIT3, at 3x
  the credits and merged to the top 10.
- Use `exact_match` only for phrases that must appear verbatim.
- For research prompts: state a clear goal, the known context, any baseline assumptions and the output format;
  avoid contradictory constraints; end with "Include citations."

### Freshness and content controls
- **Recency:** use `start_date`/`end_date` (publish date) or `time_range`. `filter_by_published_date` strictly
  drops undated results. `topic: "news"` adds dates.
- **Cache:** `max_age_hours` (added October 2026) accepts `0` for live, `-1` for never live, or 1 to 720. Leave it
  unset for stable content. On non-paying plans it can return a 434 error under load.
- **Content:**
  - `basic` and `advanced` return reranked chunks joined by `[...]`; basic has been chunked since July.
  - `include_raw_content` (markdown or text) returns the full page.
  - Extract with a `query` reranks the chunks and caps them by `chunks_per_source` (1 to 5); without a query it
    returns the whole page.

### Rate limits, pricing and pitfalls
- 1,000 RPM on paid plans, 1,500 on Growth and Scale, 100 on free. `/research` allows 20 RPM; `/crawl` and `/map`
  100 RPM. Since November 2026 the limits are shared per organisation across all keys.
  [Rate limits](https://docs.tavily.com/documentation/rate-limits)
- Credits:
  - Search: basic, fast and ultra-fast cost 1; advanced costs 2.
  - Extract: basic costs 1 and advanced 2 per 5 successful URLs.
  - Research: mini costs 4 to 110 and pro 15 to 250.
  - Dollars: PAYG $0.008 per credit; plans run $0.005 to $0.0075.
  - [Credits](https://docs.tavily.com/documentation/api-credits)
- Per question: **$0.020 to $0.032** for search (up to 4 credits) and **$0.075 to $2.00** for research (about $1
  assumed).
- Pitfalls:
  - The docs conflict on the `max_results` default (reference 10, best-practices page 5). We pin it.
  - You can get fewer results than `max_results` under strict filters.
  - `include_domains_mode` requires `include_domains`.
  - `session_id` appears in best practices but not in the reference table, so it is unverified.

### What changed since 2026-09
- July: basic search returns chunks, and `chunks_per_source` applies to basic.
- August: `include_domains_mode` (prefer or restrict), `language` and `filter_by_language`, and `safe_search` on all
  plans.
- October: `max_age_hours`, and CLI multi-query.
- November: per-organisation rate limits.
- The `fast` and `ultra-fast` depths are documented, and research gained `output_length`, `files` and
  `include_domains`/`exclude_domains` (max 20 each).
- Adapter: `TavilyProvider` maps `mode: fast` to `basic`. The new `fast` depth is a different thing; don't conflate
  them.

---

## 4. Brave

### Products and endpoints
| Product | Endpoint | Notes |
|---|---|---|
| Web Search | `GET`/`POST https://api.search.brave.com/res/v1/web/search` | Results for human consumption: `count` up to 20, `extra_snippets` (up to 5 per result). |
| **LLM Context** | `GET`/`POST /res/v1/llm/context` | Built for agents. A new content pipeline is the default; pin `Api-Version: 2026-02-06` to keep the old one. |
| Answers (AI Grounding) | `POST /res/v1/chat/completions` (OpenAI-compatible, model `brave`) | `enable_research` (multi-search) or single-search with `enable_citations`. |
| Summarizer (`summary=1`) | | Absent from the current docs pages; not used. |

### Recommended parameters
**search**: LLM Context with `q`, `count: 20`, `maximum_number_of_urls: 10`, `maximum_number_of_tokens: 16384`,
`country: us` and `search_lang: en`. All other settings stay at their defaults.
- Brave says: "For agents or chatbots, use the LLM Context endpoint"; Web Search results "are intended for human
  consumption". [Web Search](https://api-dashboard.search.brave.com/documentation/services/web-search)
- Its Context Size Guidelines: 2,048 tokens for simple facts, 8,192 standard and **16,384 for complex research**.
  "Keep the defaults for most queries; size the context with `maximum_number_of_tokens` only."
  [LLM Context](https://api-dashboard.search.brave.com/documentation/services/llm-context)
- Brave has no fetch endpoint, so there is no post-step. Per-URL text is capped by
  `maximum_number_of_tokens_per_url` (default 4,096, range 512 to 8,192).

**research**: Answers with `enable_research: true` and `stream: true`, one user message, and the vendor's
`research_*` defaults. Set a client timeout of at least 300 s.
- Brave: enable research "only when thoroughness matters more than speed, such as background tasks". Research mode
  requires streaming and cannot be combined with `enable_citations`.
  [Answers](https://api-dashboard.search.brave.com/documentation/services/answers),
  [brave-search-skills answers SKILL.md](https://raw.githubusercontent.com/brave/brave-search-skills/main/skills/answers/SKILL.md)
- Defaults:
  - `research_maximum_number_of_queries` defaults to 20 (range 1 to 50).
  - Results per query default to 30 per the official reference (changelog 2026-08-21); the skill file still says
    60, a docs conflict.
  - Iterations default to 4 and seconds to 180 (SKILL.md only).
  - `research_allow_thinking` defaults to true.

### Query writing
LLM Context `q` takes 1 to 600 chars and at most 75 words; Brave publishes no style guidance. Web Search supports
quotes, `-term`, `site:` and `filetype:` inside `q`, but we don't use operators.

### Freshness and content controls
- `freshness`: `pd`, `pw`, `pm`, `py`, or a range `YYYY-MM-DDtoYYYY-MM-DD`.
- `context_threshold_mode` (strict, balanced, lenient or disabled): use `strict` "when precision matters more
  than recall". We keep the default.
- `goggles` re-rank by domain; not used, as that would be hand-tuning.
- Handle an empty `grounding.generic` gracefully, since it means no relevant content was found.

### Rate limits, pricing and pitfalls
- Search plan: $5 per 1k requests and 50 QPS, with $5 of free credit per month; it covers LLM Context.
- Answers plan: $4 per 1k searches plus $5 per million input and output tokens, at 2 QPS.
- Source: [brave.com/search/api](https://brave.com/search/api/). The dashboard pricing page rendered empty on
  2026-11-11.
- Per question: **$0.005** for search and **about $0.10 to $1+** for research (token volume is undocumented, so
  read the `<usage>` tag).
- Pitfalls:
  - Research runs can take minutes; the SimpleQA p99 was 53 queries, 1,000 pages and about 300 s.
  - Answers requires a separate plan key.
  - Web Search's `count` caps at 20 and `offset` at 9.

### What changed since 2026-09
- The `BraveProvider` adapter calls `/web/search` with `extra_snippets`. Brave's docs now steer AI use to LLM
  Context, which is a different endpoint and response shape (`grounding.generic[]` with `snippets`, plus
  `sources{}`).
- The LLM Context pipeline changed (pin header `2026-02-06`).
- In Answers research, the results-per-query default fell from 60 to 30 (2026-08-21).
- The adapter needs an `llm_context` operation.

---

## 5. Firecrawl

### Products and endpoints
| Product | Endpoint | Notes |
|---|---|---|
| Search | `POST https://api.firecrawl.dev/v2/search` | `sources`: web, news, images or alexandria. `categories`: research, pdf, developer or gov (github was removed). |
| Scrape | `POST /v2/scrape` | Formats: markdown, json and others; `onlyMainContent`; `maxAge`; parsers. |
| Agent | `POST /v2/agent`, then `GET /v2/agent/{jobId}` | Model `spark-2`; `effort` low, medium or high; `maxCredits` defaults to 2,500. "Replaces the legacy `/extract` endpoint". |
| Deep research | none | Not in the current docs index. |

### Recommended parameters
**search**: `query`, `limit: 10`, `sources: [{type: web}]`,
`scrapeOptions: {formats: [{type: markdown}], onlyMainContent: true}` and `ignoreInvalidURLs: true`.
- One-step search plus scrape "is simpler and faster". "Don't summarize from search snippets alone."
  [Search feature](https://docs.firecrawl.dev/features/search)
- `highlights: true` is the default (query-relevant snippets from the new relevance model of 2026-07-22).
  [Search reference](https://docs.firecrawl.dev/api-reference/endpoint/search)

**research**: Agent with `prompt`, `model: "spark-2"`, `effort: "high"` and `maxCredits: 1000`, then poll.
- Specific prompts use fewer credits, and most runs use "a few hundred credits". Partial results are flagged
  `partialSchemaValid`. [Agent](https://docs.firecrawl.dev/features/agent)
- The docs don't say what each effort level changes, so `effort` is unverified. `maxCredits` 1000 is our cap;
  Stage C's 100 was below a typical run.

### Query writing
Use plain, specific queries of at most 500 chars, sent verbatim. Stage C sent a separately written
`keyword_query`; nothing in the current docs prescribes that. Use `includeDomains` or `excludeDomains` (not both),
not `site:`. Agent prompts take up to 10,000 chars.

### Freshness and content controls
- Recency: `tbs` with `qdr:h|d|w|m|y`, a custom range `cdr:1,cd_min:M/D/YYYY,cd_max:M/D/YYYY`, or `sbd:1` (sort by
  date). `tbs` filters **web only**, not news or images.
- Content: `scrapeOptions.maxAge` defaults to 2 days of cache. Parsers default to `["pdf","image"]` (PDFs cost 1
  credit per page). `onlyCleanContent` is a beta LLM pass that strips boilerplate.

### Rate limits, pricing and pitfalls
- Search per minute: Free 10, Hobby 100, Standard 500, Growth 5,000. Agent per minute: 2, 20, 100 and 1,000.
  [Rate limits](https://docs.firecrawl.dev/rate-limits.md)
- Credits: search costs 2 per 10 results, rounded up; each scraped page costs 1, each PDF page 1, and JSON mode
  +4. Agent is dynamic, with 5 free runs a day and failed runs not billed.
- PAYG dollar value per credit: Hobby $0.005, Standard $0.0025, Growth $0.002, Scale $0.001.
  [Billing](https://docs.firecrawl.dev/billing.md)
- Per question: about **12 credits** for search (plus PDF pages) and **at most 1,000 credits** for research.
- Pitfalls:
  - The SDKs return `result.web`, not `.data`.
  - `limit` applies per source type.
  - The github category now returns a 400.
  - Alexandria requires team access.

### What changed since 2026-09
- July 22: new search relevance model, with highlights on by default.
- Agent now runs on `spark-2` with `effort`; spark-1 names route to it.
- `/extract` is superseded by Agent.
- New `categories`: gov and developer. The Alexandria source and tool discovery shipped in September.
- The adapter still lists `extract: /v2/extract`, defaults to markdown (fine), and never sends `ignoreInvalidURLs`.
  Stage C scraped the top 3 in a second pass.

---

## 6. Perplexity

### Products and endpoints
| Product | Endpoint | Notes |
|---|---|---|
| Search API | `POST https://api.perplexity.ai/search` | Ranked results with page content; `search_type` web, fast or people. |
| **Agent API** | `POST /v1/agent` (alias `/v1/responses`); `GET /v1/agent/{id}` (background) | Efforts: fast, low, medium, high, xhigh and ultra. `preset` was renamed to `effort` in October. |
| Sonar Chat Completions | `/chat/completions` | **Support ended 2026-09-27.** Sync and stream calls are being reformulated into Agent API calls; async Sonar no longer works. |
| Decisions | `POST /v1/decisions` | Not used. |

### Recommended parameters
**search**: `query: [QUERY, kw1, kw2(, kw3)]`, `search_type: "web"`, `max_results: 10`, `max_tokens_per_page: 4096`
and `country: "US"`.
- Write "specific queries that include context, time frames, and precise terminology." To cover facets, pass a
  list of up to 5 related sub-queries in one request.
  [Search Best Practices](https://docs.perplexity.ai/docs/search/best-practices.md)
- `max_tokens_per_page` has no documented default. 4,096 mirrors the per-page budget that Perplexity's own effort
  presets give `web_search` (4,000; October changelog).
- `search_context_size` is **not** in the current Search API schema.
  [Search reference](https://docs.perplexity.ai/api-reference/search-post.md)

**research**: Agent with `effort: "high"`, `input: RESEARCH_BRIEF` and `background: true`, polled.
- Migration guide: `sonar-deep-research` maps to `high`.
  [Migrate from Sonar](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview.md)
- Effort page: `high` is for "deep, source-heavy research without code execution, where completeness matters more
  than latency". It runs `openai/gpt-6.1-terra` for up to 40 steps, with `web_search` and `fetch_url`.
  [Effort levels](https://docs.perplexity.ai/docs/agent-api/effort.md)
- `xhigh` and `ultra` run on `anthropic/claude-opus-5-5`, which confounds a vendor comparison; they are variants
  only.

### Query writing
- Search: natural language with precise terminology, plus multi-query lists for facets.
- Agent: `input` plus optional `instructions`. Old Sonar fields move as follows:
  - `search_domain_filter`, `search_recency_filter` and the date filters go into `tools[].filters` on `web_search`.
  - `search_context_size` becomes `max_tokens` and `max_tokens_per_page`.
  - `return_related_questions` has no equivalent and becomes a prompt pattern.
  - `search_mode: academic` has no equivalent and becomes domain filters.
  - Source: [How-to](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to.md)

### Freshness and content controls
- Search filters:
  - `search_recency_filter`: hour, day, week, month or year.
  - `search_after_date_filter` and `search_before_date_filter` (MM/DD/YYYY).
  - `last_updated_after_filter` and `last_updated_before_filter`.
  - `search_domain_filter` (at most 20; prefix `-` to exclude).
  - `search_language_filter`.
- Content: `max_tokens` covers all results (up to 1M) and `max_tokens_per_page` covers each page (up to 1M).
  `max_results` takes 1 to 20 for web search; values up to 50 require `search_type: people`.

### Rate limits, pricing and pitfalls
- Search allows 50 query units per second, where each query in a multi-query request is one unit. The Agent API
  allows 1 QPS at tier 0 up to 33 QPS at tiers 4 and 5, and polling counts against it.
  [Rate limits](https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers.md)
- Search costs $5 per 1k requests (web) or $1 per 1k (fast), with no token charge.
- Agent tools: `web_search` costs $0.0025 standard or $0.001 fast. Presets default to fast since October.
  `fetch_url` costs $0.0005. Model tokens are billed on top.
  [Pricing](https://docs.perplexity.ai/docs/getting-started/pricing.md)
- Per question: **$0.005** for search and **about $0.3 to $1** for research (unverified).
- Pitfalls:
  - Response semantics for multi-query (per-query `max_results`, ordering) are undocumented.
  - The `fast` preset bills at priority token rates (2x).
  - The `ultra` name errored in October and returned in November (`ultra@2026-11-05.0`).
  - Older OpenAI model IDs retired on 2026-10-24.

### What changed since 2026-09
- Sonar Chat Completions support ended (2026-09-27). Any Sonar or `sonar-pro`/`sonar-reasoning`/`sonar-deep-research`
  profile is obsolete; use Agent efforts.
- `preset` became `effort`; Fast Search defaults apply to presets; `max_steps` now goes up to 500.
- The adapter `PerplexityProvider` maps `max_chars` to `max_tokens_per_page` (fine). It checks for
  `search_context_size`, which the current Search schema no longer has, so drop that branch. It sends a single
  string, never a query list.

---

## 7. Native harness search

### 7a. Anthropic web search and web fetch (Messages API), and Claude Code WebSearch/WebFetch
**API tools:**
- `web_search_20250305` is basic. `web_search_20260209` adds **dynamic filtering**: Claude writes code that filters
  results before they reach context, on Claude 4.6 and later. `web_search_20260318` adds `response_inclusion`.
- Parameters: `max_uses`, `allowed_domains` or `blocked_domains` (not both), `user_location` (approximate city,
  region, country, timezone), and `allowed_callers`. From 20260209, `allowed_callers` defaults to
  `["code_execution_20260120"]`; set `["direct"]` for models without programmatic tool calling.
- Cost: $10 per 1k searches plus tokens. Failed searches are not billed. Citations are always on; `cited_text` is
  up to 150 chars.
- Continue on `pause_turn` by resending the assistant message. Results carry `encrypted_content`, which only
  Claude can read, so a non-Claude extractor cannot consume them.
- Source: [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- `web_fetch_20250910`, `_20260209` (dynamic filtering), `_20260309` (`use_cache`) and `_20260318`
  (`response_inclusion`):
  - Parameters: `max_uses` (no default limit), `allowed_domains`/`blocked_domains`, `citations.enabled` (off by
    default), `max_content_tokens` and `url_sources`.
  - Fetch can only reach URLs already present in the context, and there is no JavaScript rendering. Fetch has no
    fee beyond tokens.
  - Source: [Web fetch tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool)
- Examples use the header `anthropic-version: 2026-10-21`.

**Claude Code tools:**
- WebSearch returns titles and URLs only, and "may issue up to eight backend searches per call".
  `allowed_domains`/`blocked_domains` are available; the backend is not configurable.
- Sessions are capped at 200 searches. In `-p` mode the cap applies per turn. `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`
  raises it, and the refill rate is about 100 per hour.
- WebFetch runs a prompt-driven small-model extraction over each page ("lossy by design"). It reads up to 100k
  chars per call and caches for 15 minutes.
- The `mode: standard|extended` field seen in this session's WebSearch tool schema is not described on the
  tools-reference page, so it is unverified and we don't rely on it.
- Source: [Tools reference](https://code.claude.com/docs/en/tools-reference)

**Profiles:**
- `claude-native.search`: `claude -p` with the fixed extractor model, `WebSearch,WebFetch` and the extractor brief.
  This is a native-harness arm: retrieval and reading are one agent loop, and the reader matches the fixed
  extractor only when that extractor is Claude.
- `claude-native.research`: Messages API, `claude-opus-5-5`, `web_search_20260318` with `max_uses` 20, and
  `web_fetch_20260318` with `max_uses` 20, citations on and `max_content_tokens` 30,000.

**Changes since 2026-09:**
- Tool versions 20260209 and 20260318 exist for both search and fetch.
- Dynamic filtering is now the default caller path.
- `use_cache` was added on fetch.
- Claude Code gained the session search cap, its env vars and refill.
- The $10 per 1k price is unchanged.

### 7b. Codex CLI web search
- `web_search` takes `disabled`, `cached`, `indexed` or `live`. The default is `cached` (an OpenAI-maintained
  index with no external access). Full-access sandboxes (`--yolo`) default to `live`. `indexed` permits external
  access only when the search index gates it.
- `tools.web_search` can be a boolean or `{context_size = "low|medium|high", allowed_domains = [...], location = {...}}`.
- `features.web_search`, `features.web_search_cached` and `features.web_search_request` are deprecated toggles.
- Hosted search ignores sandbox network rules.
- Sources: [Config reference](https://learn.chatgpt.com/docs/config-file/config-reference),
  [Web search](https://learn.chatgpt.com/docs/web-search). developers.openai.com/codex now redirects to
  learn.chatgpt.com.
- Profile `codex-native.search`: `web_search="live"` and `tools.web_search={context_size="high"}`. OpenAI
  publishes no per-call price; the operator proxy is $0.005 per search.
- Changes since 2026-09: the 4-value enum including `indexed`, the `tools.web_search` object form, and the
  deprecated `features.*` toggles.

### 7c. Optional arms
- **Gemini grounding with Google Search** (`gemini-grounding.research`, optional):
  - Tool `{"type": "google_search"}` on current models (Gemini 4 Carbon and Argon, 3.x).
  - Gemini 3+ bills **per search query the model executes**: $14 per 1k after 5,000 free per month. 1,500 RPD per
    project.
  - The response carries `google_search_call` queries and `url_citation` annotations.
  - The endpoint path is unverified. [Grounding](https://aistudio.google.com/docs/google-search)
- **xAI Grok** (`xai-grok.research`, optional):
  - Responses API with the `web_search` tool: `allowed_domains` or `excluded_domains`, at most 5, not both. It
    costs $5 per 1k calls.
  - `x_search` bills per post or profile.
  - Legacy Live Search (`search_parameters`) is deprecated; the 410 status comes from third-party sources only.
  - [Search tools](https://docs.x.ai/docs/guides/tools/search-tools), [Pricing](https://docs.x.ai/developers/pricing)

---

## 8. Cross-provider table

| Arm | Search endpoint and key settings | Fetch post-step | Research endpoint and tier | Query transform | Search $/q | Research $/q | Async |
|---|---|---|---|---|---|---|---|
| Exa (3.0) | `/search` auto, dynamic highlights, n=10 | `/contents` text 20k, top 3 | Agent `high` | verbatim / brief | ~0.010 | 0.50 | poll 10 s, `/stop` |
| Parallel | `/v1/search` advanced, client_model, n=10 | `/v1/extract` full_content, top 3 | Task `ultra` + auto schema | objective + 2-3 kw / brief | 0.008 | 0.30 | poll 15 s, up to 45 min |
| Tavily | `/search` advanced, 3 chunks, n=10 | `/extract` advanced, top 3 | Research `pro`, long | verbatim / brief | 0.020-0.032 | 0.075-2.00 | poll 10 s |
| Brave | `/llm/context`, 10 URLs, 16,384 tokens | none (no fetch API) | Answers `enable_research` | verbatim / brief | 0.005 | ~0.1-1+ | SSE, 330 s |
| Firecrawl | `/v2/search`, n=10, scrape markdown on all | inline scrape | Agent spark-2 `high`, 1,000 credits | verbatim / brief | ~0.012-0.06 | <=1-5 | poll 30 s |
| Perplexity | `/search` web, [q + kw], n=10, 4,096 tokens/page | none (content inline) | Agent `high`, background | multi-query / brief | 0.005 | ~0.3-1 | poll 10 s |
| Claude native | `claude -p` WebSearch+WebFetch | in-loop | Messages + web_search/web_fetch 20260318 | harness / brief | ~0.8 (tokens) | ~1.2 | pause_turn loop |
| Codex native | `codex exec`, web_search=live, context high | in-loop | n/a | harness | ~0.5 (tokens) | n/a | none |
| Gemini (optional) | n/a | n/a | google_search tool | brief | n/a | ~0.2 | none |
| xAI (optional) | n/a | n/a | Responses + web_search | brief | n/a | ~0.2 | none |

Content delivered to the extractor differs by design. Exa, Parallel and Tavily return excerpts plus 3 full pages;
Firecrawl returns 10 full pages; Brave returns a 16k-token context; Perplexity returns up to 4k tokens per page.
Record `chars_delivered` per result so per-character efficiency can be reported beside insight counts.

## 9. Fairness rules

1. **Same top-k.** Every search arm returns at most 10 results to the extractor. Multi-query arms (Parallel,
   Perplexity) keep the first 10 unique URLs in returned order. Post-step fetches use the top 3 where the vendor
   prescribes a fetch step. Native and research arms have no top-k control; their briefs cap reading at 10 pages,
   and transcripts are audited.
2. **Same day.** Arms run side by side, each walking the questions in set order; async arms may run
   several questions concurrently. Schedule the study within one UTC day; there is no question barrier
   across arms. Freshness windows use the recorded run date.
3. **Frozen params.** The profiles in `config/insight/profiles/` are hashed into the pre-registration before the first paid call.
   No parameter changes after results are seen. Variants (EXA-S-STD, EXA-S-OBJ, EXA-D, EXA-R-XHIGH, PAR-R-PRO,
   PAR-R-RESP, TAV-S-SPLIT3, TAV-R-MINI, FC-S-TOP3, PPLX-S-SINGLE) run only if pre-registered, and are reported as
   variants.
4. **Same extractor for search arms.** One fixed extractor model, one prompt and one temperature read every search
   arm's returned text, in rank order, with vendor `notices` included (Exa) and `warnings` excluded. Native search
   arms can match this only when the extractor is that harness's model; otherwise they are reported as
   harness arms, not search arms.
5. **No hand-tuning beyond vendor docs.**
   - Questions go verbatim to every arm that accepts natural language.
   - The only rewrite is the shared query-writer (frozen prompt and model, run once, outputs stored and hashed).
     Its output goes only where a vendor's docs prescribe objective or keyword sub-queries (Parallel), or a
     facet list (Perplexity).
   - No `site:` operators, goggles, domain lists or per-question tweaks.
6. **Same research brief.** Every research arm gets the identical brief text. Arms differ only in which field
   carries it (`query`, `input`, `prompt` or `messages`).
7. **Vendor-recommended tier, cost reported.** Each research arm runs its vendor's documented deep-research tier.
   Price-matched variants are pre-registered because tiers span $0.30 to several dollars. Every table reports $
   per question and $ per verified unique insight. Measured spend (`costDollars`, `usage.credits`, `creditsUsed`,
   `<usage>`, `usage.total_cost`) wins over list prices.
8. **Caps and failures.** A run that hits its frozen cap (Firecrawl `maxCredits`, Brave seconds, our timeouts) is
   scored on what it returned, never re-run with a larger budget. Transient HTTP errors use bounded backoff (up to three retries). Paid async task POSTs
   get one attempt: a timeout or 5xx may have created a billed task, so its estimate remains charged.
   Failed cells can be retried by the next run of the command if the remaining cap permits it;
   operators should reconcile ambiguous submissions with the vendor before resuming.
9. **Pace and concurrency.** Stay under every documented rate limit, using `pace_seconds` in each profile. Never
   pace one arm to slow it relative to others.
10. **Blind grading.** As in 2026-09: pooled, deduplicated and shuffled with a recorded seed. A claim counts only
    when its own cited page states it.
