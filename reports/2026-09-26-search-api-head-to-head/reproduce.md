# Reproduce the search API head-to-head

## Check the published figures offline

These commands need no keys and make no network calls. They rebuild the scores and every table from the committed,
sanitized data and leave the record unchanged; `git diff` shows any drift. `score_b.py` rewrites
`data/stage-b/grading/b/scored_b.json` with the same content; two keys in one object can come out in a different order.

```sh
cd reports/2026-09-26-search-api-head-to-head/code
shasum -a 256 -c sets/SHA256SUMS                  # the query sets, as frozen in the pre-registration

python3 score_b.py --data ../data/stage-b         # Stage B scores
python3 make_tables_b.py --data ../data/stage-b ../record/results-stage-b.md ../record/overview.md

cd stage_c
python3 score_c.py --data ../../data/stage-c      # Stage C, as judged and corrected
python3 make_tables_c.py --data ../../data/stage-c ../../record/results-stage-c.md
cd ..
git diff --stat ..
```

Stage A's table script reads `runs/` and `grading/` next to it. Copy the committed data there to rebuild:

```sh
cp -R ../data/stage-a/runs ../data/stage-a/grading .
python3 make_tables_a.py ../record/results-stage-a.md ../record/overview.md
git diff ..                                       # only the S3 rows change; delete runs/ and grading/ afterwards
```

The S3 rows need page text, which is not published, so they come out as zeros. The per-page S3 outcomes recorded at run
time are in `data/stage-a/grading/summary_stageA_scripted.json`. Every other Stage A table rebuilds unchanged.

## Run it again

A new run measures the same method on today's web and indexes; it will not reproduce these numbers. From `code/`, put
the four keys in the environment (`EXA_API_KEY`, `TAVILY_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`); the scripts read
them from there and never write them to disk. Real runs spend money: each harness enforces the caps in its config.

```sh
python3 harness.py --set S2 --limit 2 --providers exa     # smoke test
python3 harness.py --set S1 --limit 20                    # S1 q01–q20, then the checkpoint
python3 harness.py --set S1 --offset 20 --limit 20        # S1 q21–q40
python3 harness_b.py --set S5,S6 --pairs S5:exa,S6:exa    # Stage B, by set and provider
cd stage_c && python3 harness.py --set SV && python3 harness_research.py
```

Grading needs the knowledge base the questions came from, at the baseline commit, which is not published: the pool
scripts (`grade_prep.py`, `pools_v2.py`, `grade_prep_b.py`, `stage_c/grade_prep_c.py`) take a checkout of it. With
your own knowledge base and question sets, the same scripts and judge briefs measure the same thing on your workload.
The full sequence, with each judging step, is in the record's [overview](record/overview.md#reproduce).

## The Monitors probe

`monitors_b.py create` made the five monitors on 2026-09-26. On 2026-10-06 `monitors_harvest.py harvest` found them by
name, saved every run's raw output to `runs/SM_raw/` (not published), wrote the sanitized record to
`data/stage-b/runs/SM/`, and `monitors_harvest.py pause` paused them.

```sh
python3 monitors_harvest.py status    # needs EXA_API_KEY for the account that owns the monitors
```
