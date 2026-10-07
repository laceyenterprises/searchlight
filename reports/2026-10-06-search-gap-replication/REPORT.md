# GAP replication on Claude Code, 2026-10-06: the search gap bench, run again

> **Grading note.** These verdicts come from the claude-code primary judge, which decides every
> verdict in GAP. The codex judge, which only measures agreement, was out of quota for this run;
> its agreement pass will be added here after 2026-10-09.

A second, independent Search Gap Bench battery on Claude Code (`claude-opus-5-5`), run on 2026-10-06
and 2026-10-07 with the apparatus as fixed after the [2026-10-03 battery](../2026-10-03-search-gap-bench/REPORT.md):
the built-in search arm can open pages, and every provider arm loaded its tool in every run. It repeats
that battery's method on the current task catalog: calibrate each brief, keep the tasks with a real
knowledge gap, and run every admitted task on nine arms, three times each.

**Headline.**
- **Search still decides the outcome.** Without search the agent passed 0 of 24 runs; every search arm
  passed 62–83%, and each differs from no search significantly (exact McNemar, p < 0.0001).
- **The leader changed.** Firecrawl led at 83%. Parallel, which led the 2026-10-03
  battery at 95%, passed 75%. No search arm differs significantly from the agent's
  built-in search (largest gap: Firecrawl, +8.3 points, p = 0.50).
- **Built-in search was competitive and cheapest.** It passed 75%, level with Parallel and
  Perplexity, at 55k tokens per success, the lowest of any search arm.
- **Pass rate again did not predict cost.** The rank correlation between pass rate and tokens per success across
  the seven search arms was −0.04. Firecrawl's top pass rate came at 141k tokens per success.

## Setup

|  |  |
| --- | --- |
| Harness | claude-code on claude-opus-5-5 |
| Arms | floor (no search), ceiling (answer excerpt in the prompt), native (built-in WebSearch and WebFetch), Brave, Tavily, Exa, Parallel, Firecrawl, Perplexity |
| Tasks | the 10 brief tasks of the current GAP catalog; 8 admitted by calibration |
| Repetitions | 5 per reference arm in calibration; 3 per arm in the battery |
| Grading | decision correctness, weighted key-fact recall (≥ 0.7) and unsupported-claim rate (≤ 0.25), judged blind by the claude-code primary judge against captured primary sources |
| Spend | about 13.5M agent tokens in the battery; calibration and judge usage are not totalled here |

## Calibration

| Task | Family | claude-code |
| --- | --- | --- |
| billing-reporting | research | Floor: 0, Ceiling: 5 — admitted |
| classroom-term | decision | Floor: 0, Ceiling: 5 — admitted |
| models-production | decision | Floor: 0, Ceiling: 5 — admitted |
| npm-token-scope | research | Floor: 0, Ceiling: 5 — admitted |
| org-migration | decision | Floor: 0, Ceiling: 4 — admitted |
| python-metadata | research | Floor: 0, Ceiling: 4 — admitted |
| python-security-march | research | Floor: 0, Ceiling: 5 — admitted |
| tarfile-upload | decision | Floor: 0, Ceiling: 5 — admitted |
| cli-trust | decision | Floor: 0, Ceiling: 3 — rejected |
| spark-editing | decision | Floor: 0, Ceiling: 1 — rejected |

Every floor scored 0. Two briefs were rejected for failing the ceiling, as in the 2026-10-03 calibration:
cli-trust (ceiling 3/5) and spark-editing (ceiling 1/5).
python-security-march is admitted this time (ceiling 5/5); in the 2026-10-03 calibration its answer excerpt
omitted the date its top fact requires, and the catalog has since fixed the excerpt.

## Gap closure

### claude-code (8 tasks, 24 runs per arm)

| Arm | Gap closure (95% CI) | Pass rate (Wilson 95% CI) | Tokens per success | vs floor (exact McNemar) |
| --- | --- | --- | --- | --- |
| Firecrawl | 0.91 (0.80–1.00) | 83% (64–93%) | 141k | p < 0.0001 |
| Exa | 0.86 (0.58–1.11) | 79% (60–91%) | 89k | p < 0.0001 |
| Built-in (native) | 0.82 (0.60–0.96) | 75% (55–88%) | 55k | p < 0.0001 |
| Parallel | 0.82 (0.54–1.17) | 75% (55–88%) | 90k | p < 0.0001 |
| Perplexity | 0.82 (0.64–0.96) | 75% (55–88%) | 72k | p < 0.0001 |
| Brave | 0.77 (0.50–1.06) | 71% (51–85%) | 105k | p < 0.0001 |
| Tavily | 0.68 (0.33–0.96) | 62% (43–79%) | 126k | p < 0.0001 |
| ceiling (reference) | 1.00 | 92% (74–98%) | 5k |  |
| floor (reference) | 0.00 | 0% (0–14%) | — |  |

By family:
- **Research briefs:** Firecrawl, Perplexity and Exa 92%; Parallel and Built-in 83%; Brave and Tavily 75%.
- **Decision briefs:** Firecrawl 75%; Parallel, Brave, Exa and Built-in 67%; Perplexity 58%; Tavily 50%.

## Against the 2026-10-03 battery

The task set differs by one brief (python-security-march), so the fair comparison is on the seven briefs both
batteries admitted. The 2026-10-03 built-in row is its 2026-10-06 rerun with page fetching working.

| Arm | 2026-10-03 battery (7 tasks) | Replication, same 7 tasks | Fisher exact | Replication, all 8 tasks |
| --- | --- | --- | --- | --- |
| Parallel | 20/21 (95%) | 16/21 (76%) | p = 0.18 | 18/24 (75%) |
| Firecrawl | 19/21 (90%) | 18/21 (86%) | p = 1.00 | 20/24 (83%) |
| Brave | 17/21 (81%) | 16/21 (76%) | p = 1.00 | 17/24 (71%) |
| Built-in (native) | 16/21 (76%) | 17/21 (81%) | p = 1.00 | 18/24 (75%) |
| Perplexity | 16/21 (76%) | 16/21 (76%) | p = 1.00 | 18/24 (75%) |
| Exa | 15/21 (71%) | 17/21 (81%) | p = 0.72 | 19/24 (79%) |
| Tavily | 15/21 (71%) | 15/21 (71%) | p = 1.00 | 15/24 (62%) |
| ceiling (reference) | 20/21 (95%) | 19/21 (90%) | p = 1.00 | 22/24 (92%) |

On the shared briefs no arm's change between batteries is statistically distinguishable (two-sided Fisher exact,
smallest p = 0.18); Parallel's drop from 20/21 to 16/21 is the largest. With 21 runs per arm,
neighbouring providers cannot be ranked against each other, and a single battery's ordering should not be quoted
as a ranking. On the new brief, python-security-march, the arms passed: Exa, Parallel, Firecrawl and Perplexity
2/3 each; Brave and built-in 1/3 each; Tavily 0/3 (ceiling 3/3).

## What this shows

1. **Search changes the outcome, again.** Without search the agent failed every brief; with any provider it
   passed most of them. This finding replicated exactly.
2. **Provider rankings did not replicate.** The top arm changed and the spread between search arms is within
   sampling noise at this size. Choose a provider on the agent you use, on cost and on integration, and measure.
3. **Built-in search is a serious baseline once it can read pages.** Level with the middle of the field at the
   lowest token cost per success.
4. **Pass rate and cost remain separate questions.** The cheapest arm per success and the highest-passing arm
   were different arms in both batteries.

## Limits

- One agent and model; the codex half of the 2026-10-03 design was not repeated.
- Primary-judge verdicts only until the codex agreement pass is recorded.
- 24 runs per arm: 95% intervals span roughly 30 points.
- Provider indexes, sources and models drift between batteries; a replication tests the method, not a fixed truth.

Read the [summary data](summary.json), [calibration record](calibration.json), [methodology](methodology.md) and [reproduction steps](reproduce.md).
