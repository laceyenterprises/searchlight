# Searchlight vocabulary

Searchlight tests one thing: what web search does for an AI agent doing real work. Its words are plain on purpose.
The site reads this file: the **Concepts** table feeds the "Learn Searchlight in 60 seconds" tiles on the overview
(the first eight rows) and the glossary on the Learn page; the **Report terms** table feeds the methodology glossary.
Edit the words here, then run `python3 scripts/build_site.py`.

Each glyph id names a symbol in [`glyphs/searchlight-glyphs.svg`](glyphs/searchlight-glyphs.svg).

## Concepts

| Term | Glyph | In one line | In a little more |
|---|---|---|---|
| Agent | agent | The AI coding agent doing the job: Claude Code or Codex. | The agent and its model stay the same in every run of a test. Results belong to one agent: a ranking on one did not always hold on the other. |
| Search setup | setup | The one thing that changes: a search provider, the agent's built-in search, or none. | Each provider is plugged in through its own official connector at default settings. Two controls frame them: no search at all, and the search the agent ships with. |
| Training cutoff | cutoff | The date a model's knowledge ends. | Jobs about events after the cutoff cannot be answered from memory, so they show what search adds. Jobs with well-documented answers show what it adds when memory already knows. |
| Brief | brief | A short research report that ends in a decision and cites its sources. | A brief passes with the right decision, at least 70% of the key facts and no more than a quarter of its claims unsupported by the pages it cites. |
| Run | run | One task with one search setup, repeated three times. | Every run starts in a fresh, sealed workspace, so nothing carries over. The tasks, limits and instructions are the same for every setup. |
| Blind grading | blind | The grader never sees which setup produced the answer. | Automatic checks run where a task has them. Otherwise an AI judge compares the answer with the saved source pages, without knowing the provider. |
| 95% range | range | Where the true score most likely sits. | A score from a few dozen runs is a measurement, not a fact. The range shows how far it could move if the test ran again. |
| Tie | tie | Two ranges overlap, so the test cannot separate them. | When ranges overlap, the results say so instead of naming a winner. One place higher on a list is not proof of being better. |
| No search | no-search | The control: the agent answers from memory alone. | It sets the floor. On recent events every agent scored 0% here, which is why search decides those jobs. |
| Built-in search | built-in | The search the agent ships with. | The second control. Claude Code's built-in search could not open pages in the first runs; it was fixed and rerun on 2026-10-06. |
| Gap closed | gap-closed | How far a setup got from the no-search score toward the score with the answer handed over. | 100% means it did as well as an agent given an excerpt containing the answer. Over 100% means it did better. |
| Tokens | tokens | The units AI models bill by. | Searchlight reports tokens per passing run. Dollars charged by the providers were only partly tracked. |
| Study | study | One fixed set of questions sent to every search and research service. | Studies call the services directly, with no agent, each set up as its vendor documents. Every claim is checked on the page it cites. |
| Verified | verified | The claim holds up on the page it cites. | A model reads the cited page and checks the claim against it. A claim the page does not back is unsupported; a page that cannot be read is unverifiable. |
| Strict credit | strict-credit | A service earns a finding only if its own cited page supports it. | Finding the right fact on someone else's page, or citing a page that does not say it, earns nothing. |
| Unique | unique | A finding only one service earned. | Unique findings show what a service adds that the others do not. They matter most when you can afford only one or two services. |
| Insight | insight | A verified finding that would change a decision, valued 1 to 3. | A blind judge values each verified finding for the question's reader. Generic claims are worth 0. |
| Pre-registered | pre-registered | The questions and scoring rules were fixed before any result was seen. | Fixing the rules first stops a test from being tuned to the answer it wants. |

## Report terms

The published reports keep their technical words. The site uses the plain word and gives the report term once.

| Report term | Plain word | Glyph | Meaning |
|---|---|---|---|
| arm | search setup | setup | One way of giving the agent search: one provider's connector, the agent's built-in search (native), or no search at all. |
| cell | test run | run | One task, one search setup, one repetition, graded once. |
| harness | agent | agent | The AI coding agent program: Claude Code or Codex. |
| GAP, WSB, SEW | benchmark names | benchmark | The search gap bench, the web search bakeoff, and the Search Evaluation Workbench that runs them. |
| MCP server | connector | provider | The standard connector that plugs a search provider into an agent. |
| floor, ceiling | no search, answer handed over | gap-closed | The same task with no search (floor) and with an excerpt containing the answer handed over (ceiling). A task counts only if the agent fails at the floor and passes at the ceiling. |
| gap closure | gap closed | gap-closed | How far a search setup got from the floor toward the ceiling: 1.00 means all the way. |
| judge, kappa | grader, agreement | judge | An AI model that grades answers without knowing the setup. In the search gap bench a Claude Code judge decides and a Codex judge checks; kappa measures their agreement (1 is perfect, 0 is chance). |
| tokens per success | tokens per pass | tokens | Tokens used per passing run; the search gap bench counts failed runs' tokens too. |
| Wilson 95% CI | 95% range | range | The range the true pass rate likely falls in, worked out with Wilson's method so it never runs past 0% or 100%. |
| p-value (McNemar, Fisher, sign test) | chance of a fluke | tie | How likely a difference this large would be if the setups were really equal. Below 0.05 is the usual bar for a real difference. |
| holdout | held-back questions | holdout | Questions kept back from tuning, so a result cannot be fitted to them. |
| pre-registered | fixed in advance | pre-registered | Questions and scoring rules fixed before any results were seen. |

## Wording rules

- **One plain word per idea.** Say "search setup", "test run" and "agent" on the site; give the report term once,
  where a reader will meet it.
- **Name what a number averages.** A pass rate is the share of runs that passed: say over which runs.
- **Show the range.** Every pass rate carries its 95% range. When ranges overlap, call it a tie.
- **Counts without ranges say so.** Studies that ran each question once report counts; a difference of a few items
  may be chance.
- **Put the caveat beside the number.** A note that qualifies one number sits next to that number, not in a wall up
  front.
- **The beam explains, never decorates.** The light motif appears only where it shows search finding something.
