# Agent search behavior: methodology

The analysis reads the stored transcripts of the
[web search bakeoff](../2026-09-29-web-search-bakeoff/REPORT.md) (Claude Code, eight arms,
18 tasks, three repetitions) and the
[search gap bench](../2026-10-03-search-gap-bench/REPORT.md) (Claude Code and codex, brief
tasks, three repetitions per admitted task). It runs no agent and changes no grade. Pass
and fail come from each cell's recorded evaluation.

`scripts/analyze_search_behavior.py` extracts every completed provider tool call from a
transcript:

- codex MCP tool calls, and codex built-in `web_search` items;
- Claude Code `tool_use` and `tool_result` pairs, including vendor MCP tools, WebSearch and WebFetch.

## Classification rules

- **Search call:** a call to a search tool (`web_search_exa`, `brave_web_search`,
  `tavily_search`, Parallel `web_search`, `firecrawl_search`, the Perplexity search tools,
  Claude Code WebSearch, and codex built-in `search` actions).
- **Query:** each entry of a multi-query call (Parallel `search_queries`, codex built-in query
  lists) counts as one query. Each user message in Perplexity's conversational inputs also
  counts as one query; system and assistant messages are excluded. Parallel's `objective` is
  counted as a structured parameter when `search_queries` is present, not as another query.
- **Fetch call:** a page-retrieval tool call (`web_fetch_exa`, `tavily_extract`,
  `firecrawl_scrape`, Parallel `web_fetch`, Claude Code WebFetch, and codex built-in
  `open_page`, `find_in_page` and other page views).
- **Natural-language query:** ends with "?", starts with an interrogative or auxiliary verb,
  or contains at least two function words from a fixed list (the, a, an, is, are, was, were,
  does, do, did, how, what, which, why, when, where, who, that, this, of, to, for, with,
  will, should, can, about, on, from, by, as, be, has, have).
- **Contains a year:** a token from 2000 to 2099. **Quoted phrase:** contains a double quote.
  **`site:`:** contains `site:`.
- **Fetched without searching:** the cell made at least one fetch call and no search call.
- **Primary source surfaced (GAP only):** the task's catalog oracle `source_url`, normalised
  (scheme, query, fragment and trailing slash removed), appears among the URLs in any
  returned search result of the cell, never in the search input. It is measured only over cells
  that searched. A cell is unknown when any search lacks exposed results and no observed
  result contains the primary source. Unknown cells are excluded from both surfaced and
  not-surfaced pass-rate denominators. Recomputing against returned evidence leaves the
  historical source counts unchanged; no searched cell has unknown coverage.

The rules are deliberately simple so they can be audited. The natural-language rule
undercounts descriptive keyword-free phrases and overcounts keyword strings that happen to
contain function words. Readers can substitute their own rule in the script and rerun it.

## Scope

Pooled figures cover the provider and native arms. Reference arms (no-search, floor and
ceiling) make no search calls and are excluded from query statistics. GAP cell counts
differ by harness because calibration admitted seven brief tasks for Claude Code and six
for codex.

See [the report](REPORT.md), [summary data](summary.json) and [reproduction](reproduce.md).
Calibration does not apply: [calibration record](calibration.json).
