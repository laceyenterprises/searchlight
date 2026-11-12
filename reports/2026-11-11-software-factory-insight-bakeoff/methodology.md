# Methodology: Leading software factories and agent-platform architectures (2026-11-11)

This study ran the Searchlight insight bench (INS). Every question in the set went to every arm on the same day, with each vendor's request frozen in a profile taken from that vendor's own documentation. One fixed extractor model turned each arm's returned content into atomic claims; claims were pooled and clustered per question; each claim was checked on the page it cites; and a blind judge valued the verified clusters 0-3 against the reader context. The bench's README and provider best-practices page are `docs/insight-bench/` in the Searchlight repository.

## Scoring

- **Strict credit.** An arm earns a cluster only when one of its own claims there is supported by the page that claim cites. A claim with no source counts as unsupported.
- **Insight** is an earned cluster valued 1-3 (3 decision-changing, 2 new design detail, 1 corroborating); **unique** means exactly one arm earned it.
- **Intervals.** Counts carry 95% question-bootstrap intervals, and the unsupported rate a Wilson interval; the leader is compared with each arm by an exact sign test with Holm adjustment.

## Grading

- Extractor: {'harness': 'claude-code', 'kind': 'HarnessJudgeTransport', 'model': 'claude-sonnet-5-5'}; judge: {'harness': 'claude-code', 'kind': 'HarnessJudgeTransport', 'model': 'claude-opus-5-5'}; blinding seed 20261111.
- Judging payloads carried no arm labels, the value judge saw clusters in a seeded shuffle without URLs, and tested vendors' brand names were redacted from findings.

## Arms

| Arm | Kind | Profile sha256 | Unverified fields |
| --- | --- | --- | ---: |
| brave.research | answer | `738c73636138122d` | 3 |
| brave.search | results | `91fff64549250c69` | 2 |
| claude-native.agent | imported | `99802aeff03b5105` | 0 |
| exa.research | answer | `78fce0030d8ab53c` | 1 |
| exa.search | results | `517d698c1a4a1865` | 2 |
| firecrawl.research | answer | `eae1a261624f2d7d` | 2 |
| firecrawl.search | results | `d71fd45169781154` | 1 |
| parallel.research | answer | `9671bf032d6f0edd` | 2 |
| parallel.search | results | `d143182259606be7` | 2 |
| perplexity.research | answer | `e575edb6a69c071d` | 3 |
| perplexity.search | results | `e6aecadc1d55ddb6` | 3 |
| tavily.research | answer | `afa027340c455900` | 2 |
| tavily.search | results | `50f834360fdb9cd2` | 2 |

Profiles are in [profiles/](profiles/_shared.json) and the question set in [set/](set/software-factories-2026-11.json).

## Caveats

- One run on one day: n = 24 questions at most; treat small gaps as noise.
- The extractor and judges are models; verdicts and values were not checked by hand.
- Strict credit: an arm earns a finding only if its own cited page supports it.
- 25 profile fields are unverified against vendor docs; see the profile table.
- 6 arms ran on a subset of questions (brave.research: 9, exa.research: 9, firecrawl.research: 9, parallel.research: 9, perplexity.research: 9, tavily.research: 9). Their totals cover only those questions; compare per question, and the significance tests use only questions both arms ran.
- claude-native.agent is imported: freeform research brief mapped to questions afterwards; not posed each question verbatim; 49 WebSearch + 48 WebFetch calls; cost not metered.

## Published data

`data/` holds the run manifest, the spend ledger, the shared query-writer output, every cell's request and metadata, and every grading file (claims, clusters, verdicts, values). Full page text and full vendor answer bodies are not published: result text is cut to 300-character excerpts, a cell's raw response is replaced by its length, and captured pages stay on the machine that ran the study; links to them read *local only*.
