<a href="https://searchlightai.dev"><picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/hero-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/hero-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/hero-dark.svg">
  <img src="docs/readme/hero-light.svg" alt="Searchlight. Does web search make AI agents better at real work? When the job is about something recent, search decides it: with no search the agents failed every brief; with any provider they passed most. searchlightai.dev">
</picture></a>

**An open benchmark of what web search does for AI coding agents.** Searchlight gives real agent harnesses real
jobs and changes only the search: one vendor's search server, the harness's own built-in search, or none. It grades
the finished job, not the search results, and records every tool call, so you can see *how* the agent used what it
was given. The published results cover Claude Code and codex; Hermes Agent, Opencode and Pi run on
[OSS models](#oss-models) behind a LiteLLM proxy.

**[searchlightai.dev](https://searchlightai.dev)** · [Results](https://searchlightai.dev/results.html) ·
[Methodology](https://searchlightai.dev/methodology.html) · [Reports](reports/README.md) ·
[`leaderboard.json`](site/leaderboard.json)

## Searchlight in 60 seconds

Eight ideas sit behind every number here; the [Learn page](https://searchlightai.dev/learn.html) explains each one.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/learn-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/learn-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/learn-dark.svg">
  <img src="docs/readme/learn-light.svg" alt="Eight concepts. Agent: the AI coding agent doing the job. Search setup: the one thing that changes. Training cutoff: the date a model's knowledge ends. Brief: a short research report that ends in a decision and cites its sources. Run: one task with one search setup, repeated three times. Blind grading: the grader never sees which setup produced the answer. 95% range: where the true score most likely sits. Tie: two ranges overlap, so the test cannot separate them.">
</picture>

## Results so far

Past the training cutoff, search decides the outcome; among the providers, the test cannot separate the leaders.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/results-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/results-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/results-dark.svg">
  <img src="docs/readme/results-light.svg" alt="Ranked pass rates with their 95% ranges: the top four search setups on the search gap bench for Claude Code and for Codex, and on the web search bakeoff with answering from memory as the control. On every board the leader's range overlaps the others', so the test cannot separate them.">
</picture>

- **Small samples.** Each setup ran 18 to 21 times per agent on the gap bench and 42 times on the bakeoff, so the
  ranges are wide and overlap almost everywhere: one place higher is not proof of better.
- **Rerun.** In the first runs Claude Code's built-in search could not open pages; the built-in rows are the
  2026-10-06 rerun with that fixed, and the original rows stay in the reports.
- **One interface per vendor.** Every vendor ran through its own MCP server at a pinned version with default
  settings. The separate [search API head-to-head](reports/2026-09-26-search-api-head-to-head/REPORT.md) called four
  vendors' APIs directly with tuned parameters, ran each question once, and was run for a knowledge base about Exa,
  one of the four.
- **The agent matters.** Rankings changed between the two agents.

Read [Fairness and known limitations](#fairness-and-known-limitations) before quoting a position. The
[one-page infographic](site/infographic.svg) has every finding with its evidence.

## What Searchlight measures

| Benchmark | Question it answers | Unit of work | Graded on |
|---|---|---|---|
| **WSB** (web search bakeoff) | Does an arm help an agent answer research questions correctly? | 18 production tasks, 3 repetitions | deterministic validators or a blinded rubric judge |
| **GAP** (search gap bench) | Does search change the outcome of a job whose answer post-dates the model? | calibrated briefs (and code tasks) | the finished deliverable, judged against captured primary sources |
| **DSB** (domain strength bench) | Which retrieval surface is strong in which domain? | recorded domain observations | per-domain scorecards |
| **INS** ([insight bench](docs/insight-bench/README.md)) | Which search or research API finds the most verified insight no other API finds? | a versioned question set, every arm the same day, frozen request profiles | claims verified on their own cited page, valued blind, unique insight per arm |
| **Behavior analysis** | How did the agent actually use its tool? | every logged tool call | query style, search versus fetch, primary-source retrieval |

Each run gives the agent exactly one search setup; the model, prompt, budgets and workspace stay the same.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/setups-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/setups-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/setups-dark.svg">
  <img src="docs/readme/setups-light.svg" alt="The same agent and prompt in every run (Claude Code or codex, or Hermes Agent, Opencode or Pi on an OSS model). Only the search setup changes, exactly one per run: no search, the built-in search, or the Brave, Tavily, Exa, Parallel, Firecrawl or Perplexity MCP server. The search gap bench adds two reference setups per task: the floor, no search, and the ceiling, the answer excerpt in the prompt.">
</picture>

## How a run works

One test run is one task, one search setup and one repetition, in a fresh workspace. Every tool call is recorded,
and the grader never learns the setup.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/diagram-pipeline-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/diagram-pipeline-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/diagram-pipeline-dark.svg">
  <img src="docs/readme/diagram-pipeline-light.svg" alt="From task to report: production tasks and gap briefs go into a fresh scratch workspace; the agent's tool calls pass through a metering proxy to the pinned search provider MCP server; the evidence bundle goes to deterministic validators and blinded judges; their results become reports/*/summary.json, which builds the site.">
</picture>

OSS models reach the harness through an opt-in LiteLLM proxy; see [OSS models](#oss-models).

Harness metadata lives in [`sew.harnesses`](lib/python/sew/harnesses/registry.py).
Each `HarnessSpec` defines the harness's binary, protocol, arm spawn writer and
capabilities. To add a harness, define its spec in a harness module and register
it in [`sew.harnesses.__init__`](lib/python/sew/harnesses/__init__.py). Schema
validation, live CLI choices, binary checks and spawn dispatch read live registry
views, so registrations made after import are visible to those consumers.
Protocol, spawn writer and usage parser references can use `module:attribute`
strings, resolved lazily to avoid import cycles. Judges remain explicitly pinned
to the hosted harnesses they support.

Protocols default to stdin prompts. A protocol that carries its prompt in argv
must set `takes_prompt_in_argv = True` and implement `prompt_argv`; GAP uses this
explicit capability to substitute the canary prompt while retaining qualified
launch arguments for stdin protocols.

### Anatomy of one cell

```mermaid
sequenceDiagram
  autonumber
  participant R as Runner
  participant H as Harness (Claude Code, codex, Hermes Agent, Opencode or Pi)
  participant M as Metering proxy
  participant V as Vendor MCP server
  participant A as Arm audit
  participant G as Grader
  R->>R: create scratch workspace, write arm contract
  R->>H: spawn with only the arm's tools (strict MCP config)
  loop until final answer or budget
    H->>M: tool call (search / fetch)
    M->>V: forward unchanged
    V-->>M: result
    M-->>H: result (call recorded with latency and size)
  end
  H-->>R: final answer + transcript
  R->>A: every observed tool call vs the arm contract
  A-->>R: clean, or contaminated (counted as failed, never scored)
  R->>G: answer only, arm identity withheld
  G-->>R: pass or fail with per-criterion evidence
```

### What each arm can touch

Arms differ only in their retrieval surface. Everything else (model, prompt, budgets,
workspace) is identical.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/diagram-isolation-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/diagram-isolation-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/diagram-isolation-dark.svg">
  <img src="docs/readme/diagram-isolation-light.svg" alt="What each setup can touch. A search setup contract configures each spawn. Claude Code runs with a strict MCP config and only the setup's tools; codex runs read-only with shell, browser, apps and plugins disabled. Provider setups get one provider MCP server (Claude Code adds ToolSearch and Read); built-in setups get the agent's own search; no search, floor and ceiling get no tools. A setup audit checks every observed tool call against the contract.">
</picture>

<details>
<summary>The exact spawn flags and tools per setup</summary>

```
 Claude Code                                         codex
 ───────────                                         ─────
 claude --print --output-format stream-json          codex exec --json --ephemeral
   --mcp-config <cell>/claude-mcp.json                 --sandbox read-only
   --strict-mcp-config        ← only this arm's server --disable shell_tool --disable unified_exec
   --tools <arm built-ins>    ← restricts built-ins    --disable browser_use --disable computer_use
   --allowedTools <same set>  ← pre-approves them      --disable apps --disable plugins ...
                                                       (+ --search on the native arm only)
 ┌──────────────┬──────────────────────────────┐    ┌──────────────┬───────────────────────────┐
 │ provider arm │ ToolSearch, Read             │    │ provider arm │ one MCP server            │
 │              │ + mcp__<vendor>__*           │    │              │                           │
 │ native arm   │ WebSearch, WebFetch, Read    │    │ native arm   │ built-in search and       │
 │              │                              │    │              │ page views                │
 │ no-search,   │ nothing                      │    │ no-search,   │ nothing                   │
 │ floor,       │                              │    │ floor,       │                           │
 │ ceiling      │                              │    │ ceiling      │                           │
 └──────────────┴──────────────────────────────┘    └──────────────┴───────────────────────────┘
 Read is local: Claude Code saves oversized tool results to a file the model must Read.
```

</details>

The OSS harnesses get the same contract, built from their own configuration in a fresh
per-cell home. Each runs only an explicit `litellm/<route>` model; see [OSS models](#oss-models).

| Harness | Provider arm | Native arm | no-search, floor, ceiling |
|---|---|---|---|
| Hermes Agent | one `mcp-<server>` toolset; built-in web, shell and other toolsets disabled | not applicable (no isolated native search) | nothing |
| Opencode | one local MCP server; built-in web, shell, filesystem and delegation tools denied | client-side `websearch` and `webfetch`; shell denied | nothing |
| Pi | one MCP server through Searchlight's stdio bridge; built-in tools, extensions and skills off | not applicable (Pi has no web tools) | nothing |

A not-applicable cell is recorded as such without launching the harness.

After every cell an **arm audit** compares each observed tool call with the arm contract.
A call outside the contract (for example, a provider arm reaching native search) marks the
cell contaminated. A contaminated cell counts as a failure and is never scored as a pass.

### Code tasks run inside an egress sandbox

GAP code tasks give the agent a real repository, a shell and an offline wheel cache. The
agent must not download the fixed package version, or read it from the verifier's copy.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ host                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ egress sandbox: Seatbelt (macOS) or bubblewrap (Linux)                  │ │
│  │   network: deny all  ·  canary probes must be refused before the cell   │ │
│  │  ┌───────────────────────────────────────────────────────────────────┐  │ │
│  │  │ agent workspace: repo + old/dependency wheels only (no fixed one) │  │ │
│  │  │   pip forced offline; package commands audited                    │  │ │
│  │  └───────────────────────────────────────────────────────────────────┘  │ │
│  │   verifier cache (old + new wheels): read-denied to the agent           │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│  search arm traffic leaves only through the arm's MCP server                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

Cells whose isolation cannot be established are refused, not run. See
[RUNBOOK-gap.md](RUNBOOK-gap.md) for the full isolation contract.

## GAP: calibration and gap closure

A GAP task counts only if it is out of the model's reach without search, and within reach
when the answer is handed over. Calibration is repeated for each harness and model.
Calibration defers temporarily unavailable cells and stops resumably on a provider
wall; see [Validate and calibrate](RUNBOOK-gap.md#validate-and-calibrate) for recovery.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/diagram-calibration-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/diagram-calibration-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/diagram-calibration-dark.svg">
  <img src="docs/readme/diagram-calibration-light.svg" alt="Which tasks count: a candidate brief about an event after 2026-01-01 runs five times with no search (the floor) and five times with the answer excerpt in the prompt (the ceiling). It is admitted for this agent and model only if the floor passes at most once and the ceiling at least four times; otherwise it is rejected. Admitted tasks run in the battery, every search setup three times, and give each setup's gap closure.">
</picture>

```
gap closure  =  (arm pass rate − floor pass rate) / (ceiling pass rate − floor pass rate)

   floor                         arm                              ceiling
   0%  ●────────────────────────────●──────────────────────────────●  95%
       └─────────── closed ─────────┘└────────── remaining ────────┘
   1.00 = the arm did as well as handing the agent the answer. It can exceed 1.
```

## Grading

Two judges see the same inputs; neither is told the search setup.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/diagram-grading-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/diagram-grading-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/diagram-grading-dark.svg">
  <img src="docs/readme/diagram-grading-light.svg" alt="How answers are graded: the agent's JSON answer goes to a schema validator, which rejects malformed answers; its cited URLs go to source capture; the readable source text and rubric, blinded to the search setup, go to judge 1 (Claude Code) and the same inputs to judge 2 (codex, gap bench). Judge 1 returns key-fact recall, unsupported claims and the decision; judge 2 returns independent labels, and their agreement is recorded. A brief passes with the right decision, recall of at least 0.7 and unsupported claims of at most 0.25.">
</picture>

- **WSB** uses deterministic validators where a task has one. Otherwise a single blinded
  Claude Code judge scores the task rubric.
- **Statistics:**
  - Pass rates carry Wilson 95% intervals.
  - GAP compares each arm with the floor by an exact McNemar test (paired by task and repetition).
  - GAP gap-closure intervals use a seeded paired task bootstrap.
- **GAP tokens per success** counts every agent token in an arm, failed cells included, divided by
  the number of passing cells. **WSB tokens per success** uses measured tokens from graded cells
  divided by measured successes; ungraded and unmeasured cells are excluded, and per-arm
  coverage counts were not retained.

## Measuring how agents search

`scripts/analyze_search_behavior.py` reads stored transcripts. It makes no network or model calls.
It reads each harness's own event stream: Claude Code and codex, Opencode `tool_use` events,
Hermes Agent tool calls and Pi tool results. Harnesses prefix a vendor's tool with its server
name in different ways (`mcp__exa__web_search_exa`, `mcp_exa_web_search_exa`,
`exa_web_search_exa`); the analyzer drops the prefix, so every harness's call counts as
the vendor's `web_search_exa`. Opencode's native `websearch` and `webfetch` count as search and fetch.
Failed Pi calls and calls interrupted before their results arrive still count as calls
and contribute their queries. They provide no returned evidence: source coverage stays
unknown unless another observed result contains the oracle URL.

```mermaid
flowchart LR
  B[("run bundles")] --> E["extract tool calls, including interrupted calls"]
  E --> C{"search or fetch?"}
  C -->|search| Q["queries: natural language, year, quotes, site:, parameters"]
  C -->|fetch| F["fetch-only cells: fetched without searching"]
  E --> X["harness refusals"]
  Q --> O["primary source surfaced? (GAP catalog oracle URL)"]
  O --> P["pass rate with vs without the primary source"]
```

The rules are deliberately simple and are printed in the
[methodology](reports/2026-10-05-agent-search-behavior/methodology.md). Because the
natural-language rule can miss descriptive phrasing, every Exa query was also checked by
hand; Exa is the tool that explicitly asks for page descriptions. All 111 are published in
the report's appendix.

## The search API head-to-head

A separate study called four search APIs directly, with no agent, on pre-registered questions, each run once, so it
reports counts without ranges. Its [report](reports/2026-09-26-search-api-head-to-head/REPORT.md) publishes the
record, code and data.

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/readme/diagram-direct-tall-dark.svg">
  <source media="(max-width: 600px)" srcset="docs/readme/diagram-direct-tall-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/diagram-direct-dark.svg">
  <img src="docs/readme/diagram-direct-light.svg" alt="The search API head-to-head: pre-registered question sets from one knowledge base go to Exa, Tavily, Parallel and Firecrawl; their results are pooled, deduplicated and shuffled; blind model judges verify each claim on its page and check whether it is already in the knowledge base; attribution is rejoined, crediting a provider only where its own page states the claim; the results are reports/*/summary.json.">
</picture>

## Fairness and known limitations

Engineers from every tested service will read this, so the limitations come first.

- **Small samples.**
  - Each arm ran 18 to 21 cells per harness on GAP and 42 on the WSB competitive set.
  - Adjacent positions are not rankings.
  - No WSB arm differs from answering without search at 95% confidence.
- **One interface per vendor.** Each vendor ran through its own MCP server at the version
  pinned in [`config/provider-arm-tools.yaml`](config/provider-arm-tools.yaml), with default
  settings. Every tool the server advertised was available. In the agent benchmarks, direct
  APIs, other modes, and vendor features the MCP server does not expose were not tested. For
  example, Exa's advanced search tool was not enabled, and Parallel's hosted server ran without a
  pinned mode. The [search API head-to-head](reports/2026-09-26-search-api-head-to-head/REPORT.md)
  tested the APIs directly, with no agent; its results do not transfer to agents, or back.
- **The agent matters.** Parallel led on Claude Code and tied for last on codex. A provider's
  position on one harness does not transfer to another.
- **Native-arm defect (corrected).**
  - Claude Code's native arm exposed WebFetch but pre-approved only WebSearch.
  - Headless Claude Code refused those WebFetch calls: in every GAP native cell and in 40 of 54
    WSB native cells.
  - Its native rows understate Claude Code's own web tools, and are flagged everywhere they appear.
  - The arm configuration is fixed and pinned by a test.
- **Agents fetch from memory.** On WSB's documented-knowledge tasks, Claude Code often
  fetched remembered URLs without searching. Arms with a fetch tool therefore measured fetching
  as much as search.
- **Cost.**
  - GAP tokens per success counts all agent tokens, failed cells included. WSB excludes
    ungraded and unmeasured cells; its per-arm coverage counts were not retained.
  - Vendor dollar spend was metered only on WSB, and only where a tariff or reported spend existed.
  - GAP batteries ran with vendor metering off.
  - Dollar comparisons are therefore not published in the leaderboard. Pricing coverage per
    vendor is reported separately.
- **Grading.** Judges are models: Claude Code on both benchmarks, plus codex as a second judge
  on GAP. They see sources and rubrics, never the arm.
- **Task mix.**
  - GAP admitted 7 brief tasks for Claude Code and 6 for codex, so cross-harness comparisons
    mix agent and task effects.
  - WSB is mostly stable, documented knowledge, where search adds least.
- **Corrections are published beside the original numbers, never silently replaced.** If you
  maintain a tested service and see something wrong, see [For vendors](#for-vendors-corrections-and-re-runs).

### Provider configurations used

| Arm | Interface | Pinned version | Tools exposed |
|---|---|---|---|
| Brave | stdio MCP server | `@brave/brave-search-mcp-server@2.1.4` | 8 (`brave_web_search`, `brave_llm_context`, news, local, place, image, video, summarizer) |
| Tavily | stdio MCP server | `tavily-mcp@0.2.22` | 5 (search, extract, crawl, map, research) |
| Exa | stdio MCP server | `exa-mcp-server@3.4.1` | 2 (`web_search_exa`, `web_fetch_exa`) at defaults: `auto`, 10 results, highlights |
| Parallel | hosted MCP via `mcp-remote@0.14.3` | `search.parallel.ai/mcp-oauth` | 2 (`web_search`, `web_fetch`), no mode override |
| Firecrawl | stdio MCP server | `firecrawl-mcp@3.26.0` | 29 (search, scrape, crawl, map, extract, agent, research, monitor, …) |
| Perplexity | stdio MCP server | `@perplexity-ai/mcp-server@1.3.0` | 4 (search, ask, research, reason) |
| native | harness built-in | Claude Code 2.1.282 / codex `--search` | WebSearch + WebFetch (Claude Code); built-in search and page views (codex) |
| native (OSS) | harness built-in | Opencode >= 1.17.3 | `websearch` + `webfetch` (Opencode); not applicable on Hermes Agent and Pi |

## Quickstart

```sh
pipx install 'git+https://github.com/laceyenterprises/searchlight'
sew doctor          # mode, state root, key presence, harness logins, sandbox qualification
```

`searchlight` and `sew` are the same command.

```
 ┌──────────────── standalone (default) ────────────────┐   ┌─────── agent-os (optional) ──────┐
 │ provider keys:  SEW_<VENDOR>_API_KEY or a local .env │   │ keys via a host secrets service  │
 │ harness auth:   your own `claude` / `codex` login    │   │ harness auth via a token broker  │
 │ OSS models:     a LiteLLM proxy you run              │   │ LiteLLM key via host secrets     │
 │ state:          ~/.local/share/sew                   │   │ installs with the [agent-os]     │
 │ selected by:    SEW_MODE=standalone or auto          │   │ extra; never required            │
 └──────────────────────────────────────────────────────┘   └──────────────────────────────────┘
```

Provider keys come from environment variables: `SEW_EXA_API_KEY`, `SEW_PARALLEL_WEB_API_KEY`,
`SEW_FIRECRAWL_API_KEY`, `SEW_BRAVE_API_KEY`, `SEW_BRAVE_ANSWERS_API_KEY`, `SEW_TAVILY_API_KEY`
and `SEW_PERPLEXITY_API_KEY`.
- An untracked `.env` also works (`SEW_ENV_FILE` selects it).
- `SEW_*_API_KEY_REF=env:NAME` references another variable.
- Never commit keys.
- Live runs require `SEW_HARNESS_LIVE=1` and spend model and provider quota.

```sh
# offline: recorded fixtures, no keys, no network
sew run --suite lighthouse --mode fixture

# live web search bakeoff (budgeted)
SEW_HARNESS_LIVE=1 sew run --suite wsb-trial --mode live \
  --provider-mcp-config /path/to/provider-mcp.yaml \
  --max-provider-calls 400 --max-provider-result-chars 1000000 \
  --max-total-tokens 3000000 --max-wall-clock-seconds 14400
sew bakeoff report /path/to/suite-run

# GAP: calibrate first, then run the battery, per harness and model
SEW_HARNESS_LIVE=1 sew gap calibrate --harness codex --model MODEL_ID --reps 5 --wheelhouse /path/to/wheels
SEW_HARNESS_LIVE=1 sew gap run --harness codex --model MODEL_ID --arm floor --arm ceiling --arm native \
  --reps 3 --wheelhouse /path/to/wheels --run-root /path/to/gap-battery
sew gap report /path/to/gap-battery

# how did the agents search?
python3 scripts/analyze_search_behavior.py /path/to/suite-run --catalog catalogs/gap/tasks.yaml --json out.json
```

Hermes Agent, Opencode and Pi, and OSS models on any harness, need the opt-in LiteLLM
setup in [OSS models](#oss-models).

In live runs, `--max-wall-clock-seconds` sets the run wall-clock limit;
`timeouts.run_seconds` is the fallback when no operator budgets are supplied.
Suite spend budgets remain ceilings on the operator caps. Elapsed wall time
persists across resumes, so raise the operator wall-clock cap to continue a run
that stopped at its previous cap. The run summary reports `budget_limits` and
`budget_elapsed_seconds`.

The provider MCP configuration is a YAML file outside the repository, and it names keys by
environment reference. See [configuration and metering](WORKBENCH.md) and
[RUNBOOK-gap.md](RUNBOOK-gap.md) before any live execution.

## OSS models

Searchlight can run open-weight and other non-frontier models through a
[LiteLLM](https://docs.litellm.ai/) proxy that you run. OSS support is off by
default, and nothing in this section is needed for the published results or for
`sew doctor` in standalone mode.

```mermaid
flowchart LR
  C["config/oss-models.yaml: model ids, limits, dated rates"] --> R["runner: one cell per harness x model x arm"]
  Y["sew.yaml oss: enabled, LiteLLM endpoint, key variable"] --> R
  R --> H["harness: Hermes Agent, Opencode, Pi, Claude Code or codex"]
  H <-->|"model calls, scoped proxy key"| L["LiteLLM proxy (yours)"]
  L <--> M[("hosted OSS APIs or a self-hosted model")]
  H <-->|"tool calls"| V[("the arm's MCP server")]
  R --> B["bakeoff report: $ per cell labelled with its rate basis"]
```

### Harnesses

| Harness | id | Minimum version | Binary override | Models | Native arm | GAP |
|---|---|---|---|---|---|---|
| Claude Code | `claude-code` | | `SEW_CLAUDE_CODE_BIN` | its own, or a `litellm/<route>` | WebSearch + WebFetch; not applicable on an OSS model | yes |
| codex | `codex` | | `SEW_CODEX_BIN` | its own, or a `litellm/<route>` | `--search`; not applicable on an OSS model | yes |
| Hermes Agent | `hermes` | 0.16.0 | `SEW_HERMES_BIN` | a `litellm/<route>` only | not applicable | not yet |
| Opencode | `opencode` | 1.17.3 | `SEW_OPENCODE_BIN` | a `litellm/<route>` only | `websearch` + `webfetch` | not yet |
| Pi | `pi` | 0.79.8 (and Node.js) | `SEW_PI_BIN` | a `litellm/<route>` only | not applicable | not yet |

`sew doctor` checks each OSS harness's version with `--version`, without a model call.
GAP calibration and batteries accept `claude-code` and `codex`; code cells on the OSS
harnesses are refused.

Grok is registered as a non-live harness. Search arms explicitly report
unavailable until their tool isolation is qualified. On an entitled fleet host,
`sew.harnesses.grok.fleet_cells(opt_in=True)` delegates native smoke to the
shared fleet adapter; the lock is `config/locks/grok.json`. With the adapter
installed, every role/auth cell is unavailable without opt-in. If the adapter
or its dependencies cannot be imported, `fleet_cells()` raises a descriptive
`sew.schema.SchemaError`; importing Searchlight does not require the adapter.
Operator provisioning and MODEL-TURN
receipts belong to GRK-16; hammer remains pending GRK-17. These cells do not
qualify search arms or native resume.

### Setup

1. **Install LiteLLM.** It is a separate runtime dependency, not a Searchlight package
   requirement. Install and run it outside Searchlight; the
   [example route list](config/litellm-example.yaml) maps every catalog route to an
   upstream model and reads each upstream key from LiteLLM's own environment. It holds
   no credentials.
2. **Pick models.** The [OSS catalog](config/oss-models.yaml) contains the seven
   `litellm/<route>` model ids, their context and output limits, and dated rates. A
   route's LiteLLM `model_name` must match the catalog route.
3. **Configure `sew.yaml`:**

   ```yaml
   oss:
     enabled: false
     litellm:
       base_url: http://127.0.0.1:4000
       api_key_env: SEW_LITELLM_API_KEY
     harnesses: [hermes, pi, opencode, claude-code, codex]
     # models defaults to every route in config/oss-models.yaml
   ```

4. **Set the environment:**

   | Variable | Effect | Default |
   |---|---|---|
   | `SEW_OSS_ENABLED` | `1`/`true` or `0`/`false`; overrides `oss.enabled` | `oss.enabled`, else off |
   | `SEW_LITELLM_BASE_URL` | the proxy endpoint, HTTP(S) without credentials; overrides `oss.litellm.base_url` | `http://127.0.0.1:4000` |
   | `SEW_LITELLM_API_KEY` | the proxy key handed to each cell; `oss.litellm.api_key_env` names a different variable | none |

5. **Check and preview.** `sew doctor` probes LiteLLM only when OSS is enabled, then
   preview a cell without reading a key or starting a harness:

   ```sh
   SEW_OSS_ENABLED=1 sew run-live-harness --harness opencode --model litellm/glm-5.2 --arm exa --dry-run
   ```

A suite selects OSS models per harness by naming the route as a model profile:

```yaml
harnesses:
  opencode:
    model_profiles: [litellm/glm-5.2]
  claude-code:
    model_profiles: [default, litellm/local/qwen3-coder-next-80b-a3b-6bit]
```

### How OSS cells run

Credentials are injected into each cell, and judges keep their own credentials.
`sew doctor` checks `/health/readiness` and lists selected routes advertised by
`/v1/models`. These diagnostics use the proxy key when provisioned and report
availability without displaying the key. They do not make a model call or establish
that a listed route can complete inference.
Claude Code and Codex route catalogued OSS models through LiteLLM for provider
search arms. Claude Code receives the proxy endpoint and token per cell; Codex
uses an isolated LiteLLM Responses provider in its cell config. Native search
cells are recorded as not applicable without launching a harness. Token usage may
record `usage_basis` as `harness`, `litellm_response`, or `unavailable`. Legacy Pi
profiles live under `fixtures/` solely for offline replay.

### Cost and reports

Model cost evidence uses the catalog rates. Each catalog entry has a `rate_basis`:

- `list`: the provider's published rate on the entry's `as_of` date, from its `source`.
- `self-hosted`: $0 per token. Hardware and power are not counted, so a self-hosted
  dollar figure is not a cost of ownership.

Once a run includes an OSS model, the bakeoff report's cost table adds harness and
model columns and labels every OSS dollar figure with its basis, for example
`$1.8400 (catalog 2026-10-06)` or `$0.0000 (self-hosted)`, followed by a list of each
model's rate and source. Reports without OSS models render exactly as before. The
site's GAP leaderboards and infographic draw one board per harness the report
contains, titled with the model its setup table names.
The infographic wraps these boards into rows with at least 120 pixels of plotting
space per panel, and expands the finding's height to fit every row.

### Hermes Agent search cells

Hermes Agent search cells require Hermes >= 0.16.0 and the opt-in OSS
configuration described above. Set `SEW_HERMES_BIN` to override its binary.
Select an explicit `litellm/<route>` model. Each cell creates a fresh
`HERMES_HOME/config.yaml`, uses a custom OpenAI-compatible provider with the
proxy key read from the child environment, and exposes only the selected MCP
server. Built-in web, shell and other toolsets are disabled. Hermes's native
arm is unavailable in this adapter. GAP code cells add the `terminal` and `file`
toolsets under the bench's portable sandbox; see
[OSS harness code cells](lib/python/sew/gap/WORKSPACE.md#ohm-08-oss-harness-code-cells).

The adapter uses `hermes -z PROMPT -m ROUTE --provider custom:searchlight
--ignore-rules` (plus `-t SERVER` for provider arms), verified with 0.16.0
`hermes --help`. One-shot stdout contains only final text; the driver polls
`HERMES_HOME/state.db` for machine-readable messages, tool calls and session
usage. A separate thread reads stdout; the driver waits at most 50 ms for queued
stdout before polling the ledger, so silent or partial-line output cannot block
live budget checks. Session readiness and usage updates feed the existing boot
and budget checks; usage is known only to the extent Hermes has persisted it.
Raw session databases and configuration homes are temporary and are removed
after the cell.
See the upstream [MCP configuration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/)
and [toolsets reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference).
This adds support only; no model runs are required to validate the adapter.

### Opencode search cells

The [Opencode adapter](docs/opencode.md) supports isolated search cells on
Opencode >= 1.17.3 with OSS models, including its client-side native web tools.

### Pi search cells (support only)

Pi requires version 0.79.8 or newer, Node.js, an enabled `oss` configuration,
a catalog `litellm/<route>` model and a scoped LiteLLM key. `sew doctor` checks
its version; `SEW_PI_BIN` selects an alternate binary. Preview a cell without
calling a model:

```sh
sew run-live-harness --harness pi --model litellm/glm-5.2 --arm exa --dry-run
```

The version probe has a ten-second timeout. Launch errors, timeouts and nonzero
probe exits propagate from cell setup as operational exceptions, allowing the
caller to retry; they are not converted to version configuration refusals.
`sew doctor` reports these as `version probe failed` and continues its checks.
Setup can be repeated in the same scratch directory after a partial failure;
the shipped extensions and cell configuration are rewritten on each attempt.

Each cell uses a fresh `PI_CODING_AGENT_DIR`, Searchlight's LiteLLM provider
extension and a stdio MCP bridge for the selected arm only. The live runner
resolves the LiteLLM key through the host credential service (in standalone
mode, from `SEW_LITELLM_API_KEY` or the configured `oss.litellm.api_key_env`)
and injects it into Pi as `SEW_LITELLM_API_KEY`. The extension reads this
normalized child variable; credentials are not written to the cell config.
Built-in tools, extension discovery, skills and project context are disabled. JSON events
supply the answer, usage and arm audit; model pricing uses the OSS catalog.
Pi has no native web tools, so its native arm is not applicable. The offline
Pi profiles and fixture driver remain available. The legacy `pi-live-smoke`
help probe no longer creates a live-success bundle. GAP code cells keep Pi's
default built-ins (read, bash, edit, write) under the bench's portable sandbox; see
[OSS harness code cells](lib/python/sew/gap/WORKSPACE.md#ohm-08-oss-harness-code-cells).

MCP tool failures remain errors and include the server's diagnostic text in
the exception delivered to Pi; non-text content blocks are serialized as JSON.
The MCP server inherits Pi's stderr, so initialization and runtime diagnostics
are captured in the cell's `artifacts/harness-stderr.txt`.
The bridge ignores blank stdout lines and skips malformed JSON with a fixed
stderr warning that omits the line contents. Pending requests remain active
and retain their thirty-second timeout.
Cell evidence scrubs the JSON server-env envelope and values under credential
keys, while preserving benign settings such as `DEBUG`, `PORT` and `NODE_ENV`.

## Reproducing the published results

Each report directory contains `reproduce.md`, with exact historical and shipped catalog
hashes. Raw transcripts and provider responses are not distributed, so the published
aggregates cannot be replayed exactly. The one exception is the search API head-to-head's
Monitors probe, whose raw responses are published with its report. A new live run measures the same method on today's
models, indexes and sources.

New WSB bundles created by `sew bakeoff bundle` include the OSS catalog at
`manifests/config/oss-models.yaml` alongside the price table and task manifests.
Report regeneration, verification and task explanation use that snapshot for
both OSS token prices and rate labels, so installed catalog changes do not alter
the bundled report.

```sh
python3 scripts/check_reports.py        # transcription, scrub, catalog hashes, links
python3 scripts/build_site.py --check   # pages, infographic, share image and README art match the reports
SEW_MODE=standalone python3 -m pytest -q
```

When running the suite inside a sandbox, tests that need unavailable containment
capabilities skip with a reason. Set `SEW_REQUIRE_CONTAINMENT_TESTS=1` to make
those capabilities mandatory on supported platforms (as in Linux CI).
See the [CI change note](docs/changes/SLCI-01.md) for the standalone matrix,
parallel suite and runtime cap.

### How the leaderboard stays current

The results page displays search setups, test runs and agents in place of internal
benchmark terms. Source reports, machine-readable fields and link anchors retain
their original names.

The builder accepts integer or decimal percentage intervals with hyphens or en dashes.
Zero-count groups display 0% with their counts; short table rows receive empty cells,
and code blocks missing a closing fence retain their content. Relative links are bounded
at the repository root. The site draws its diagrams itself and loads no diagram script.

The README images in [`docs/readme/`](docs/readme/) come from the same build: the hero, the
results snapshot, the drawn diagrams and the reports timeline are generated from the reports,
in a light and a dark variant, and `build_site.py --check` fails when any of them is stale.
[The design system](design/README.md#readme-art) explains how they are drawn.

The share image (`site/social-card.png`, used by link previews) is rendered from
`site/social-card.html`, whose figures come from the same reports. After a report
changes, run `python3 scripts/build_site.py` and then `python3 scripts/render_social_card.py`
(needs Google Chrome or Chromium; set `CHROME` if it is not found). The image carries the
SHA-256 of the card it was rendered from, so `build_site.py --check` flags a stale image
without a browser. Rendering fails immediately if Chrome exits without a complete PNG;
a running browser has 120 seconds to finish. Process-group signal errors during cleanup
do not mask the render result or its original failure.

```mermaid
flowchart LR
  N["new battery"] --> R["reports/DATE-BENCH/ with summary.json, methodology, reproduce"]
  R --> C["check_reports.py: transcription, scrub, hashes, links"]
  C --> B["build_site.py: site pages, infographic, leaderboard.json, README art"]
  B --> S["render_social_card.py: share image"]
  S --> K["CI: build_site.py --check"]
  K --> P["GitHub Pages deploy from main"]
```

## For vendors: corrections and re-runs

```mermaid
flowchart LR
  I["issue: configuration or grading concern"] --> T["triage within the repo"]
  T --> F["config or grader fix, with a test"]
  F --> R["re-run of the affected arms"]
  R --> P["new report published beside the original; the original stays, marked"]
```

Open an issue with the configuration you recommend (server version, tools, settings) or the
grading decision you dispute, and include a cell from the published tables. Corrections
are re-run and published as new, dated reports. Earlier numbers stay visible, with a note
pointing to the correction.

## Repository layout

```
searchlight/
├── bin/                     hq-sew wrapper, wheel-cache preparation
├── catalogs/                task catalogs: production (WSB), gap, domains, fixtures
├── config/                  pinned provider arm tools, price table, MCP meter tariffs,
│                            OSS model catalog, example LiteLLM routes
├── design/                  design system: tokens, glyphs, marks, vocabulary
├── docs/                    insight bench, architecture notes; readme/ holds the generated README art
├── lib/python/sew/          runner, arms, harness adapters, meter, grading, reports
│   └── gap/                 calibration, workspace sandbox, verifier, brief grading
├── reports/                 published reports (summary.json is the source of truth)
├── scripts/                 check_reports, build_site, analyze_search_behavior, gates
├── site/                    generated website: overview, results, methodology, infographic, logo
├── tests/                   offline test suite (recorded fixtures)
├── RUNBOOK-gap.md           GAP isolation contract and operations
└── WORKBENCH.md             detailed engineering reference and design log
```

## Provenance, license, security

- Searchlight was extracted from an internal search evaluation workbench; see
  [PROVENANCE.md](PROVENANCE.md).
- Licensed under [Apache-2.0](LICENSE).
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md).
## What this module promises

`python3 scripts/smoke.py` is the canonical standalone sandbox command, forwarded by
installed `hq smoke searchlight`. The command and path selectors in
`post-merge-verify.json` are relative to the Agent OS superproject root, where
Searchlight is registered at `tools/searchlight`. In a standalone clone, use
`python3 scripts/smoke.py` from this repository root, or the absolute script path
from any directory. `scripts/smv-smoke.py` forwards to the same runner. Neither
standalone entry point requires the Agent OS shared Python helpers.

Maintainers regenerating the README state diagrams with `sew.smv.render()` need
the Agent OS superproject environment, with `modules/worker-pool/lib/python` on
`PYTHONPATH`. In standalone clones the helper reports this requirement before
writing generated files; the smoke commands remain independently runnable.

- `existing-harness-fixture-matrix` (`fixture.matrix`): Reuse run_acceptance_fixture_matrix and validate its four synthetic evidence bundles through the real schema reader.
  The step also proves `fixture-run` through the real CLI, `truthful-evaluation`
  through the real evaluator (a passing fixture and a failing timed-out run),
  and `unique-resume` through two successive real fixture cells with distinct keys.
- `durable-resume-without-duplicates` (`index.resume`): Run a tiny real fixture matrix, resume it, and read back the unchanged completed index.
- `provider-wall-stays-incomplete` (`index.defer`): Drive the real runner through an injected unavailable provider streak, then resume healthy synthetic execution without losing evidence.

The harness uses disposable stores and synthetic external boundaries. Required missing, failed or unavailable evidence exits nonzero. An empty declared step list also fails, since no promises were verified. No ambient credentials or endpoints are accepted by the CLI. Host mode is unavailable until a safe canary is approved; SRE instead inspects installed version and authoritative recent tool receipts read-only after deployment. SRE verifies service owner, launchd domain and documented restart path before any separately authorized activation. The existing main-catchup declaration lane owns page-once module/step/commit failures, with no revert and no later-deploy hold. No scheduled workflow changes.

The invocation alarm allows 170 seconds within the declared 180-second budget. Deadline cancellation stops remaining steps and records failure diagnostics with object-valued `observed` evidence. The alarm is disarmed when the run returns, before disposable HOME cleanup and receipt output, so those final operations can emit the JSON report within the remaining budget.

<!-- smv:state-machines:start -->

### State machine contracts

Generated from `state-machines.json`. Missing enforcement describes existing dead ends;
the listed owners and durations are proposed contracts, not implemented repairs.

#### searchlight.run

```mermaid
stateDiagram-v2
    state "budget_exhausted" as s_6275646765745f657868617573746564
    state "cancelled" as s_63616e63656c6c6564
    state "contaminated" as s_636f6e74616d696e61746564
    state "failed" as s_6661696c6564
    state "harness_boot_failed" as s_6861726e6573735f626f6f745f6661696c6564
    state "not_applicable" as s_6e6f745f6170706c696361626c65
    state "provider_unavailable" as s_70726f76696465725f756e617661696c61626c65
    state "succeeded" as s_737563636565646564
    state "timeout" as s_74696d656f7574
    state "unsupported" as s_756e737570706f72746564
    [*] --> s_6275646765745f657868617573746564: searchlight.run.budget_exhausted
    [*] --> s_63616e63656c6c6564: searchlight.run.cancelled
    [*] --> s_636f6e74616d696e61746564: searchlight.run.contaminated
    [*] --> s_6661696c6564: searchlight.run.failed
    [*] --> s_6861726e6573735f626f6f745f6661696c6564: searchlight.run.harness_boot_failed
    [*] --> s_6e6f745f6170706c696361626c65: searchlight.run.not_applicable
    [*] --> s_70726f76696465725f756e617661696c61626c65: searchlight.run.provider_unavailable
    [*] --> s_737563636565646564: searchlight.run.succeeded
    [*] --> s_74696d656f7574: searchlight.run.timeout
    [*] --> s_756e737570706f72746564: searchlight.run.unsupported
    s_6275646765745f657868617573746564 --> [*]: searchlight.run.end.budget_exhausted
    s_63616e63656c6c6564 --> [*]: searchlight.run.end.cancelled
    s_636f6e74616d696e61746564 --> [*]: searchlight.run.end.contaminated
    s_6661696c6564 --> [*]: searchlight.run.end.failed
    s_6861726e6573735f626f6f745f6661696c6564 --> [*]: searchlight.run.end.harness_boot_failed
    s_6e6f745f6170706c696361626c65 --> [*]: searchlight.run.end.not_applicable
    s_70726f76696465725f756e617661696c61626c65 --> [*]: searchlight.run.end.provider_unavailable
    s_737563636565646564 --> [*]: searchlight.run.end.succeeded
    s_74696d656f7574 --> [*]: searchlight.run.end.timeout
    s_756e737570706f72746564 --> [*]: searchlight.run.end.unsupported
    s_6661696c6564 --> s_70726f76696465725f756e617661696c61626c65: searchlight.run.unavailable.failed
    s_6861726e6573735f626f6f745f6661696c6564 --> s_70726f76696465725f756e617661696c61626c65: searchlight.run.unavailable.harness_boot_failed
    s_737563636565646564 --> s_70726f76696465725f756e617661696c61626c65: searchlight.run.unavailable.succeeded
    s_74696d656f7574 --> s_70726f76696465725f756e617661696c61626c65: searchlight.run.unavailable.timeout
    s_6275646765745f657868617573746564 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.budget_exhausted
    s_63616e63656c6c6564 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.cancelled
    s_6661696c6564 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.failed
    s_6861726e6573735f626f6f745f6661696c6564 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.harness_boot_failed
    s_6e6f745f6170706c696361626c65 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.not_applicable
    s_70726f76696465725f756e617661696c61626c65 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.provider_unavailable
    s_737563636565646564 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.succeeded
    s_74696d656f7574 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.timeout
    s_756e737570706f72746564 --> s_636f6e74616d696e61746564: searchlight.run.contaminated.unsupported
    s_737563636565646564 --> s_6275646765745f657868617573746564: searchlight.run.budget
```

#### searchlight.provider-call

```mermaid
stateDiagram-v2
    state "failed" as s_6661696c6564
    state "not_applicable" as s_6e6f745f6170706c696361626c65
    state "ok" as s_6f6b
    state "rate_limited" as s_726174655f6c696d69746564
    state "refused" as s_72656675736564
    state "timeout" as s_74696d656f7574
    [*] --> s_6661696c6564: searchlight.provider-call.failed
    [*] --> s_6e6f745f6170706c696361626c65: searchlight.provider-call.not_applicable
    [*] --> s_6f6b: searchlight.provider-call.ok
    [*] --> s_726174655f6c696d69746564: searchlight.provider-call.rate_limited
    [*] --> s_72656675736564: searchlight.provider-call.refused
    [*] --> s_74696d656f7574: searchlight.provider-call.timeout
    s_6661696c6564 --> [*]: searchlight.provider-call.end.failed
    s_6e6f745f6170706c696361626c65 --> [*]: searchlight.provider-call.end.not_applicable
    s_6f6b --> [*]: searchlight.provider-call.end.ok
    s_726174655f6c696d69746564 --> [*]: searchlight.provider-call.end.rate_limited
    s_72656675736564 --> [*]: searchlight.provider-call.end.refused
    s_74696d656f7574 --> [*]: searchlight.provider-call.end.timeout
```

#### searchlight.provider-result

```mermaid
stateDiagram-v2
    state "failed" as s_6661696c6564
    state "not_applicable" as s_6e6f745f6170706c696361626c65
    state "ok" as s_6f6b
    state "rate_limited" as s_726174655f6c696d69746564
    state "refused" as s_72656675736564
    state "timeout" as s_74696d656f7574
    state "unavailable" as s_756e617661696c61626c65
    [*] --> s_6661696c6564: searchlight.provider-result.failed
    [*] --> s_6e6f745f6170706c696361626c65: searchlight.provider-result.not_applicable
    [*] --> s_6f6b: searchlight.provider-result.ok
    [*] --> s_726174655f6c696d69746564: searchlight.provider-result.rate_limited
    [*] --> s_72656675736564: searchlight.provider-result.refused
    [*] --> s_74696d656f7574: searchlight.provider-result.timeout
    [*] --> s_756e617661696c61626c65: searchlight.provider-result.unavailable
    s_6661696c6564 --> [*]: searchlight.provider-result.end.failed
    s_6e6f745f6170706c696361626c65 --> [*]: searchlight.provider-result.end.not_applicable
    s_6f6b --> [*]: searchlight.provider-result.end.ok
    s_726174655f6c696d69746564 --> [*]: searchlight.provider-result.end.rate_limited
    s_72656675736564 --> [*]: searchlight.provider-result.end.refused
    s_74696d656f7574 --> [*]: searchlight.provider-result.end.timeout
    s_756e617661696c61626c65 --> [*]: searchlight.provider-result.end.unavailable
```

#### searchlight.evaluation

```mermaid
stateDiagram-v2
    state "fail" as s_6661696c
    state "not_applicable" as s_6e6f745f6170706c696361626c65
    state "pass" as s_70617373
    [*] --> s_6661696c: searchlight.evaluation.fail
    [*] --> s_6e6f745f6170706c696361626c65: searchlight.evaluation.not_applicable
    [*] --> s_70617373: searchlight.evaluation.pass
    s_6661696c --> [*]: searchlight.evaluation.end.fail
    s_6e6f745f6170706c696361626c65 --> [*]: searchlight.evaluation.end.not_applicable
    s_70617373 --> [*]: searchlight.evaluation.end.pass
```

#### searchlight.index

```mermaid
stateDiagram-v2
    state "deferred" as s_6465666572726564
    state "indexed" as s_696e6465786564
    state "unindexed" as s_756e696e6465786564
    [*] --> s_756e696e6465786564: searchlight.index.create
    s_756e696e6465786564 --> s_696e6465786564: searchlight.index.persist
    s_696e6465786564 --> s_6465666572726564: searchlight.index.defer
    s_756e696e6465786564 --> s_6465666572726564: searchlight.index.defer-unindexed
    s_6465666572726564 --> s_696e6465786564: searchlight.index.resume
    s_696e6465786564 --> s_696e6465786564: searchlight.index.replay
```

#### searchlight.brief-grade

```mermaid
stateDiagram-v2
    state "judge_unavailable" as s_6a756467655f756e617661696c61626c65
    state "schema_invalid" as s_736368656d615f696e76616c6964
    state "scored" as s_73636f726564
    [*] --> s_6a756467655f756e617661696c61626c65: searchlight.brief-grade.judge_unavailable
    [*] --> s_736368656d615f696e76616c6964: searchlight.brief-grade.schema_invalid
    [*] --> s_73636f726564: searchlight.brief-grade.scored
    s_6a756467655f756e617661696c61626c65 --> [*]: searchlight.brief-grade.end.judge_unavailable
    s_736368656d615f696e76616c6964 --> [*]: searchlight.brief-grade.end.schema_invalid
    s_73636f726564 --> [*]: searchlight.brief-grade.end.scored
```

### Exit owners and known dead ends

| Object | Non-terminal states | Proposed exit owner | Proposed maximum | Recipient | Enforcement |
|---|---|---|---|---|---|
| searchlight.index | `deferred` | searchlight maintainer/caller | 180s | searchlight maintainer | missing |
| searchlight.index | `indexed` | searchlight maintainer/caller | 180s | searchlight maintainer | missing |
| searchlight.index | `unindexed` | searchlight maintainer/caller | 180s | searchlight maintainer | missing |

Each missing row has source evidence in its definition. No general overdue enforcer is added here.
Proposed lifetime/expiry bounds above are not repairs. See each state deadEndEvidence selector.

### Ownership dispositions

| Field | Owner | Disposition | Evidence |
|---|---|---|---|
| `RUN_STATUSES / harness STATUS_* and TERMINAL_* / run.json` | tools/searchlight | owned | Run bundles are terminal snapshots, including GAP contamination/provider/budget reclassification; terminal markers are harness observations, independently inventoried. |
| `runner-state.json index/deferred_unavailable; gap/runner.py entries/deferred/attempts` | tools/searchlight | owned | Durable membership and resumable attempts; status snapshots reference searchlight.run. Cancelled GAP cells and deferred cells may be retried. No mandatory resume actor or retention sweeper; enforcement=missing. Proposed owner audit within 180s. |
| `PROVIDER_CALL_STATUSES / SCHEMA_STATUS_BY_RESULT_STATUS; retrieval_lane/agent_lane cells` | tools/searchlight | owned/derived | Provider result and normalized call records are mapped separately. Retrieval/agent cells project results; async pending/completed IDs belong to external providers, not a Searchlight job engine. |
| `EVALUATION_OUTCOMES; gap/brief_grade.py status; agreement measured/not_measured; verify JUnit statuses` | tools/searchlight | owned/derived | Immutable outcome/brief result snapshots are mapped. Agreement, source coverage, benchmark/report/index aggregates and capture statuses are derived observations; missing judgment is never success. |
| `schema config enums; catalog/model profiles; token accounting; provider availability; live auth/egress/containment probes` | tools/searchlight | configuration/reference | Configuration and diagnostics are not extra durable status lifecycles. Pure hash/parser/render helpers own no state machine. Existing live/host probes remain explicitly manual; fixture smoke cannot certify paid execution or platform containment. |

The Python AST inventory independently reads actual enums, status writers and mutation guards.
The normal runner suite rejects changed state names, changed index edges and missing contract fields.
Snapshots/observations do not transfer ownership from the referenced runtime. Synthetic smoke evidence does not prove live dispatch or provider behavior.

<!-- smv:state-machines:end -->
