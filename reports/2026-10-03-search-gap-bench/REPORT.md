# GAP battery results, 2026-10-03: does search change the outcome of the job?

> **Update (2026-10-06): the Claude Code native arm was rerun with page fetching working.** In the
> original battery the arm exposed WebSearch and WebFetch but pre-approved only WebSearch, and
> headless Claude Code refused all 50 WebFetch calls across its 21 cells, so that row measured
> search without page fetching. The configuration is fixed and pinned by a test. The arm's 21 cells
> were rerun on 2026-10-06 on the same 7 tasks, repetitions, model and calibration; no call was
> refused. The Claude Code native row and every figure derived from it below use the rerun:
> 16/21 passed (76%), gap closure 0.80 (0.53–1.00), 61k tokens per success. The original row
> recorded 13/21 (62%), gap closure 0.65 (0.37–0.91) and 66k. The other arms are unchanged.
> The rerun's verdicts come from the claude-code primary judge, which decides every verdict in this
> battery; codex agreement for those 21 cells is pending until codex quota returns (2026-10-09).
> The rerun's agents used about 1.0M tokens. The codex native arm was unaffected.
> Details: [agent search behavior](../2026-10-05-agent-search-behavior/REPORT.md#native-rerun-2026-10-06).

The first full Search Gap Bench (GAP) battery ran on 2026-10-03, operator-approved: brief tasks on two harnesses, nine arms each, three repetitions.
- GAP grades the job, not the retrieval.
- A task counts only after calibration shows its knowledge gap: the no-search arm (floor) passes at most 1 of 5, and the arm given the answer excerpt (ceiling) passes at least 4 of 5.
- Each search arm is scored by **gap closure**: how much of the floor-to-ceiling distance it covers.
- Tokens are reported beside every outcome.

**Headline.** On these tasks, search decides the outcome.
- The floor passed **0%** on both harnesses, and every search arm passed 71–95%.
- Which provider is best depends on the harness: Parallel led on claude-code and Brave on codex.
- Pass rate did not predict cost: providers with similar pass rates differed almost twofold in tokens per success.

These are the recorded results after the Codex reruns. Claude-code provider tool availability remains unverified in the published evidence: no transcript audit for those cells is documented, and they lack availability records. Its provider rankings are provisional pending that audit. In the first codex pass, 31 of 108 provider cells ran without their search tool. They were re-run on 2026-10-04 under the GAPMCP-01 fix, every re-run cell loaded its tool, and the codex tables below include them (see "Execution changes").

## Setup

|  |  |
| --- | --- |
| Harnesses | claude-code on claude-opus-5-5, codex on gpt-6.1-sol |
| Arms | floor (no search), ceiling (answer excerpt in the prompt), native, Brave, Tavily, Exa, Parallel, Firecrawl, Perplexity |
| Tasks | 10 brief tasks from GAP-09: 6 decision briefs and 4 research briefs. The code corpus (GAP-07) merged during the run and gets its own battery. |
| Repetitions | 5 per reference arm in calibration; 3 per arm in the battery |
| Grading | Decision correctness, weighted key-fact recall (≥ 0.7) and unsupported-claim rate (≤ 0.25), judged by two blinded judges (claude-code primary, codex secondary) against captured primary sources |
| Spend | about 50M tokens in total: battery agents 21.1M; battery judges 15.3M; calibration agents 4.2M; calibration judges 9.2M |

## Calibration

Final admissions under GAPRULE-01. Each cell shows floor passes and ceiling passes out of 5.

| Task | Family | claude-code | codex |
| --- | --- | --- | --- |
| billing-reporting | research | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 5 — admitted |
| classroom-term | decision | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 5 — admitted |
| models-production | decision | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 5 — admitted |
| npm-token-scope | research | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 5 — admitted |
| org-migration | decision | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 4 — admitted |
| python-metadata | research | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 5 — admitted |
| tarfile-upload | decision | Floor: 0, Ceiling: 5 — admitted | Floor: 0, Ceiling: 0 — rejected |
| cli-trust | decision | Floor: 0, Ceiling: 2 — rejected | Floor: 0, Ceiling: 2 — rejected |
| spark-editing | decision | Floor: 0, Ceiling: 1 — rejected | Floor: 0, Ceiling: 0 — rejected |
| python-security-march | research | Floor: 0, Ceiling: 0 — rejected | Floor: 0, Ceiling: 1 — rejected |

Every floor scored 0, so every task has a real knowledge gap. All rejections are ceiling failures, which point at task or rubric defects:
- **spark-editing:** the heaviest key fact is not what the prompt asks about.
- **python-security-march:** the excerpt omitted the date that its top fact requires. The excerpt fix is pending for the next battery; GAPRULE-01 changed the unsupported-claim limit, not the excerpt.
- **cli-trust:** with three claims, a single unsupported claim fails the brief.
- **tarfile-upload on codex:** the codex briefs miss key facts.

## Gap closure

### claude-code (7 tasks, 21 cells per arm)

| Arm | Gap closure (95% CI) | Pass rate (Wilson 95% CI) | Tokens per success | vs floor (exact McNemar) |
| --- | --- | --- | --- | --- |
| Parallel | 1.00 (0.86–1.17) | 95% (77–99%) | 64k | p < 0.0001 |
| Firecrawl | 0.95 (0.83–1.00) | 90% (71–97%) | 116k | p < 0.0001 |
| Brave | 0.85 (0.62–1.00) | 81% (60–92%) | 93k | p < 0.0001 |
| Perplexity | 0.80 (0.50–1.00) | 76% (55–89%) | 74k | p < 0.0001 |
| native | 0.80 (0.53–1.00) | 76% (55–89%) | 61k | p < 0.0001 |
| Exa | 0.75 (0.47–0.95) | 71% (50–86%) | 89k | p < 0.0001 |
| Tavily | 0.75 (0.43–1.00) | 71% (50–86%) | 83k | p < 0.0001 |
| ceiling (reference) | 1.00 | 95% | 4k |  |
| floor (reference) | 0.00 | 0% | — |  |

By family:
- **Research briefs:** every search arm passed 100%, native included.
- **Decision briefs:** they separate the providers. Parallel 92% and Firecrawl 83% lead; Brave scored 67%, Perplexity and native 58% each, and Exa and Tavily 50% each.

### codex (6 tasks, 18 cells per arm)

| Arm | Gap closure (95% CI) | Pass rate (Wilson 95% CI) | Tokens per success | vs floor (exact McNemar) |
| --- | --- | --- | --- | --- |
| Brave | 1.06 (0.88–1.29) | 94% (74–99%) | 111k | p < 0.0001 |
| Firecrawl | 1.00 (1.00–1.00) | 89% (67–97%) | 166k | p < 0.0001 |
| Tavily | 1.00 (0.71–1.29) | 89% (67–97%) | 129k | p < 0.0001 |
| Exa | 0.88 (0.47–1.29) | 78% (55–91%) | 146k | p = 0.0001 |
| native | 0.88 (0.47–1.20) | 78% (55–91%) | 117k | p = 0.0001 |
| Parallel | 0.81 (0.41–1.14) | 72% (49–88%) | 157k | p = 0.0002 |
| Perplexity | 0.81 (0.47–1.12) | 72% (49–88%) | 117k | p = 0.0002 |
| ceiling (reference) | 1.00 | 89% (67–97%) | 12k |  |
| floor (reference) | 0.00 | 0% (0–18%) | — |  |

No search arm beat native significantly; the largest gain was Brave at +16.7 points (p = 0.25).

By family:
- **Research briefs:** every arm passed 100%, except Exa at 89%.
- **Decision briefs:** they separate the providers. Brave scored 89%; Firecrawl and Tavily 78% each; Exa 67%; native 56%; Parallel and Perplexity 44% each.

The first pass's estimates that excluded the 31 tool-less cells were close to the re-run results:

| Arm | Estimate | After re-run |
| --- | --- | --- |
| Brave | 93% | 94% |
| Firecrawl | 85% | 89% |
| Tavily | 85% | 89% |
| Exa | 76% | 78% |
| Parallel | 67% | 72% |
| Perplexity | 62% | 72% |

## What this shows

1. **On these tasks, search changes the outcome.** Without search, both harnesses failed every brief. With a good provider they reached the answer-excerpt arm's level. The difference against the floor is significant for every search arm on both harnesses (exact McNemar, p ≤ 0.0002).
2. **The best provider depends on the harness.** On claude-code, Parallel (95%) and Firecrawl (90%) led. On codex, Brave led (94%), ahead of Firecrawl and Tavily (89% each).
   - Parallel tied for last on codex (72%), even with its tool loaded in every cell. On codex decision briefs it passed 44%, against 92% on claude-code.
   - So a provider's ranking on one harness does not carry over to another.
3. **Pass rate does not predict cost between providers.** Tokens per success counts every agent token in the arm, failed cells included, divided by passing cells.
   - The hypothesis that better search shows up as both lower token spend and higher success holds against no search, which spent tokens and passed nothing. On claude-code, Parallel paired the top pass rate (95%) with 64k tokens per success; only native search, at 76%, spent less per success (61k).
   - It does not hold as a ranking. Among the seven search arms, the rank correlation between pass rate and tokens per success was +0.05 on claude-code and −0.20 on codex; negative means higher-passing arms cost less. With seven arms, neither value is distinguishable from zero.
   - Per-cell spend varied widely: 46k (native) to 105k (Firecrawl) on claude-code, and 84k (Perplexity) to 148k (Firecrawl) on codex. Firecrawl passed 90% at 116k tokens per success, against Parallel's 95% at 64k.
   - Choosing a provider means weighing pass rate and cost separately.
4. **Decision briefs separate the providers; research briefs mostly don't.** Research briefs passed at or near 100% on both harnesses. Decision briefs hinge on one recent fact, and they spread the search arms from 50% to 92% on claude-code and from 44% to 89% on codex.
5. **Native search was mid-pack on both harnesses.** On claude-code it passed 76%, level with Perplexity and behind Parallel, Firecrawl and Brave. On codex it passed 78%, level with Exa and behind Brave, Firecrawl and Tavily. No search arm beat it significantly on either harness; on claude-code the largest gain was Parallel's, +19.0 points (p = 0.22).

## Changes made during the run

The first live runs surfaced several bench defects. Two changed how cells **execute**, and two changed how cells are **graded**. The timeline was recorded from the battery change log and cell start events.

### Execution changes

Every cell in the tables above ran under both of these fixes:
- **GAPBUDGET-01:** brief budgets became runaway caps (1M tokens, 60 calls). The old 30k cap was below the median codex search cell.
  - It was part of the pinned export before any calibration or battery cell ran.
  - No cell ran under the old caps.
- **GAPARM-01:** brief search arms resolve the GAP catalog. Before it, no brief search arm could run, so no search-arm cell predates it.
  - It was applied at 18:30:04Z on 2026-10-03.
  - The earliest search-arm cell started at 18:30:29Z (codex round 1). claude-code started at 19:12Z.

The tool-isolation remediation, which forbids the GAP catalog to every GAP cell, was applied at 18:50:40Z and the codex battery was restarted to load it.
- **18 codex round-1 cells** started before it: classroom-term repetitions 1 and 2, all arms.
- **No catalog access in them.** Their transcripts contain only search calls and agent messages, with no file reads or commands, so the catalog was not read.
- **They were kept, not re-run.**
- **Every other cell** ran after the remediation.

**The codex provider re-run (2026-10-04).** In the first pass, 31 of codex's 108 provider cells made no provider call:

| Arm | Cells without a provider call |
| --- | --- |
| Parallel | 12 |
| Firecrawl | 5 |
| Tavily | 5 |
| Perplexity | 5 |
| Brave | 3 |
| Exa | 1 |

- **Cause:** GAPMCP-01 reproduced codex's 30-second default MCP startup timeout. A 35-second server fails under it and starts with a 120-second allowance.
- **How the cells were selected:** the 31 cells were reclassified from an audit of their transcripts. All of them had zero provider calls and a `list_mcp_resources` probe; 29 of the 31 also said in their answer that no retrieval was available. The private audit is not distributed.
- **How they were re-run:** with `--rerun-unavailable` under GAPMCP-01 (a 120-second startup allowance, `required=true` and pre-warmed pinned packages), using the same matrix, calibration and catalog.
- **Every re-run cell loaded its tool.** Each one recorded a completed `initialize` and tool listing.
- **A second fix was needed.** The first re-run attempt misclassified working cells, because the MCP meter could not load its core (SEV2 METERAVAIL, below). Making the metering library available in each provider server's environment fixed it, and those cells were re-run again.

These 31 cells ran under the startup allowance; the other 77 provider cells ran without it but had their tool. No agent-side setting changed.

### Grading changes

These changed only how stored answers were judged. Every affected cell was regraded from the agents' stored answers, with no agent re-runs:

- **GAPJUDGE-01:** judges read readable text instead of raw HTML, and each source once. This cut judge tokens per cell from about 250k to about 15k. It also covered:
  - safe redirects;
  - gzip decoding (python.org served gzip unconditionally, which garbled every python.org citation);
  - over-budget sources withheld instead of the whole cell going ungraded;
  - the blinding leak check reading decoded text, and generic arm labels no longer counted as leaks.
- **GAPRULE-01, operator-approved:** the unsupported-claim limit was raised from 0.10 to 0.25. Ceiling agents see a short excerpt, and their hedges about it ("the notice does not specify X") were contradicted by the full pages the judges read. All stored grades were recomputed from their saved judge labels; 49 outcomes flipped, all from fail to pass.

## Open issues

- **SEV2 GAPMCP (fix GAPMCP-01, the bench fix):** resolved. The 31 affected codex cells were re-run and the codex table updated.
- **SEV2 METERAVAIL (fix METERAVAIL-01):** provider-call metering was silently off for this battery on both harnesses. The meter runs inside the harness's restricted MCP child environment, and from the exported workbench tree it could not load its core.
  - Vendor spend per cell was therefore not metered.
  - Only the 31 re-run cells have availability records; the report's "availability coverage" column counts those. Claude-code provider availability has not been verified in the published evidence.
- **SEV2 ARGVKEY (fix ARGVKEY-01):** the Parallel API key was passed in `mcp-remote`'s process arguments. The battery configuration now passes it through a 0600 header file.
- **The claude-code floor arm** sometimes writes pretend searches as text when it has no tools. It is a legitimate failure, but slow and expensive (up to about 130k output tokens in 13 minutes).
- **Next battery:**
  - the code corpus (GAP-07: 16 code tasks and 4 controls);
  - the python-security-march excerpt fix;
  - rubric fixes for spark-editing and cli-trust.

Read the [summary data](summary.json), [calibration record](calibration.json), [methodology](methodology.md) and [reproduction steps](reproduce.md).
