# SM probe grading: Exa Monitors, 2026-09-27 to 2026-10-06

**Method.** Each result was checked against the vendor's live page (2026-10-06), the nearest Internet Archive captures before and after the run (via CDX), and, for Parallel and Perplexity, changelog RSS timestamps. Each was then compared with the KB at `origin/main` (29731be). Rules: `grading.json` → `criteria`.

| Monitor | Run date | Kind | Real | In KB |
|---|---|---|---|---|
| m02 Parallel | 09-29 | product | confirmed | yes |
| m02 | 10-01 | pricing | confirmed | no |
| m02 | 10-03 | other | confirmed | partial |
| m02 | 10-03 | pricing | confirmed | no |
| m03 Firecrawl | 09-30 | pricing | false_positive | yes |
| m03 | 10-01 | pricing | false_positive | no |
| m03 | 10-01 | product | confirmed | no |
| m03 | 10-02 | product | confirmed | no |
| m03 | 10-06 | pricing | false_positive | yes |
| m03 | 10-06 | product | confirmed | no |
| m04 Perplexity | 09-28 | product | confirmed | no |
| m04 | 09-28 | pricing | confirmed | partial |
| m04 | 09-29 | product | confirmed | no |
| m04 | 09-30 | pricing | confirmed | no |
| m04 | 10-01 | other | not_confirmed | no |
| m04 | 10-02 | pricing | confirmed | no |
| m04 | 10-05 | pricing | confirmed | partial |
| m04 | 10-05 | pricing | confirmed | partial |
| m05 Brave | 10-01 | pricing | false_positive | no |
| m05 | 10-01 | pricing | false_positive | no |

**Totals:** 14 confirmed, 1 not confirmed, 5 false positives. 13 qualify; 12 of those are new or partly new to the KB.

**Verdicts (real dated change / new to KB)**

- **m01 Tavily: no / no.** No results. Tavily's changelog has nothing after August.
- **m02 Parallel: yes / yes.** Fast-processor pricing table removed; Image Search pricing added. The /v1/extract "breaking change" is a docs note on April 2026 behaviour.
- **m03 Firecrawl: yes / yes.** Alexandria enrichment, Agent's preview label dropped, fantasy-sports data. The add/remove/add SSO results are noise: the Enterprise card is unchanged in every capture from 09-26 to 10-05.
- **m04 Perplexity: yes / yes.** xhigh moved to Opus 5.5, two new models, Sonar pricing withdrawn, Decisions and image_search pricing, Fast Search preset price cut.
- **m05 Brave: no / no.** Live prices are unchanged. The URLs now also serve a headings-only markdown version, the likely cause.

**Limits**

- One grader, not blinded.
- The Archive was intermittently offline.
- No Brave captures since August, and no Firecrawl pricing capture from 10-01 to 10-05.
- The pre-rename Decisions model name is unverified.
- A later Perplexity Decisions price cut, after the last run, is not graded.
- Misses weren't measured. Firecrawl's 09-29 changelog entry was never reported.
