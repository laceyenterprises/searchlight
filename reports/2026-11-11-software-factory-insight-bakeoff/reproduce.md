# Reproduce: Leading software factories and agent-platform architectures

A new live run evaluates the method again: the web, the vendors' indexes and the models change, so its numbers will differ. These are the inputs and commands this run used.

| Input | sha256 |
| --- | --- |
| question set `software-factories-2026-11` | `b262c19f5cc8b393cf53029ec53c7c2d2844b51504a3624db952a20b0f370f52` |
| shared templates `_shared.json` | `f5c37dde8f6be178ed49208c7b856917d864efb9401dfa2d548bec449589236f` |
| profile `brave.research` | `738c73636138122dcc67d6c1370b9f1550ab3168750a7c21770cd8e02d67c46e` |
| profile `brave.search` | `91fff64549250c699fb0cddbda0e7ced96b75eed2e87959b01e34fd57cc1e281` |
| profile `claude-native.agent` | `99802aeff03b5105fc91385c5d60ba3e1fcd2e219660fc7d5852402a66c81bd4` |
| profile `exa.research` | `78fce0030d8ab53cf3a98d52cfa3b2ee6cf93905a4f6b12e51194002c8103fb1` |
| profile `exa.search` | `517d698c1a4a1865c8a65382bf33761e9282d5f81c27af91b646e59641534a52` |
| profile `firecrawl.research` | `eae1a261624f2d7d61edbffbfd657801bf749f3f81af530163afec1dcf6f0c42` |
| profile `firecrawl.search` | `d71fd451697811547b33724b478eedffa022727d815b86030edc794c852bfa82` |
| profile `parallel.research` | `9671bf032d6f0edd6927b228b7b7c7466f1908133b17daa81313654a2994dbbd` |
| profile `parallel.search` | `d143182259606be78b7cf033bd5ca3e5f47d821d9ede59572c1bd4a88342ff05` |
| profile `perplexity.research` | `e575edb6a69c071dd2d4c3d0cff8b9511895bb4681f4f039b98b946b1ee068a7` |
| profile `perplexity.search` | `e6aecadc1d55ddb6e8a95404baeae397bf8db8d2642a6273bd49e5205decfe57` |
| profile `tavily.research` | `afa027340c4559002d9497ee104fb19f1f9889c3041898e6f581fd7ae4f5456e` |
| profile `tavily.search` | `50f834360fdb9cd2def870ace0decc4254209316d5e55c485d387e0ddf05fd45` |

```sh
sew insight run --set software-factories-2026-11 --arms brave.research,brave.search,exa.research,exa.search,firecrawl.research,firecrawl.search,parallel.research,parallel.search,perplexity.research,perplexity.search,tavily.research,tavily.search --extractor-model claude-sonnet-5-5 \
  --max-usd 39.9 --dry-run   # then again with SEW_HARNESS_LIVE=1
sew insight import --run RUN --arm claude-native.agent --claims CLAIMS.jsonl --label "Claude Code native WebSearch/WebFetch (Opus 5.5 research agent)" --note "freeform research brief mapped to questions afterwards; not posed each question verbatim; 49 WebSearch + 48 WebFetch calls; cost not metered"
SEW_HARNESS_LIVE=1 sew insight grade --run RUN --extractor-model claude-sonnet-5-5 --judge-model claude-opus-5-5
sew insight report --run RUN
sew insight publish --run RUN --to reports/YYYY-MM-DD-SLUG/
```

Keys come from `SEW_*_API_KEY` variables or `*_API_KEY_REF` references; none are stored here.
