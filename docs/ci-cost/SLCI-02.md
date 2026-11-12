# SLCI-02: CI cost across every event

Snapshot: 2026-11-11 23:31 UTC, starting at origin/main
`0054508e096a7c4b4a1870917c3ae7ffac82298d`. Window:
2026-10-12 23:31 UTC inclusive through 2026-11-11 23:31 UTC exclusive.
[Source measurements](SLCI-02.json) retain every run and job, timestamps,
conclusions, the slowest job's steps, and 50 local test phases.

## Observed cost

Read-only REST requests through `gh api`, with pagination, covered **all**
repository workflows. Runner-minutes sum job start-to-completion durations,
including failed/cancelled jobs and all attempts (`jobs?filter=all`).
They exclude queue time and are unweighted elapsed runner time, not rounded
invoice minutes or macOS price multipliers. Outcome shares count runs.

| Event | Runs | Jobs | Runner-minutes | Failure share | Cancel share |
| --- | ---: | ---: | ---: | ---: | ---: |
| pull_request | 44 | 130 | 4,798.87 | 4/44 (9.1%) | 15/44 (34.1%) |
| push | 19 | 55 | 2,245.07 | 0/19 (0%) | 2/19 (10.5%) |
| schedule | 4 | 24 | 575.05 | 4/4 (100%) | 0/4 (0%) |
| workflow_dispatch | 0 | 0 | 0.00 | N/A | N/A |
| Total | 67 | 209 | 7,618.98 | 8/67 (11.9%) | 17/67 (25.4%) |

Push includes 18 Standalone runs / 54 jobs / 2,244.75 minutes and one Pages
run / one job / 0.32 minutes. Pages is retained, with pip caching added.
Cancelled runs consumed **748.00 minutes**. The existing PR-number/ref plus
event concurrency group and `cancel-in-progress: true` already cancel
superseded PR pushes without cross-cancelling scheduled/manual coverage;
these semantics remain unchanged. No reruns or manual dispatches were started.

The [slowest job](https://github.com/laceyenterprises/searchlight/actions/runs/40095202766/job/120876113052)
took 3,604 seconds:

| Step | Seconds | Share of job |
| --- | ---: | ---: |
| Fresh installed clone suite without external network | 3,581 | 99.36% |
| Provision Linux sandbox | 10 | 0.28% |
| Install public dependencies and local package | 6 | 0.17% |
| Lint | 3 | 0.08% |
| Set up job | 1 | 0.03% |
| Checkout | 1 | 0.03% |
| Other timed steps | 0 | 0.00% |
| Unattributed job overhead | 2 | 0.06% |

## Before and after

These are **measurement-derived planning estimates, not completed hosted
after measurements**. SLCI-01 had no completed xdist run at the snapshot.
Successful historical PR runs averaged 138.652 minutes (three Pythons),
and main pushes averaged 139.570: **278.22 minutes per historical merge pair**.
The historical PR Python 3.12 job averaged 43.896 minutes over 25 successes.
Using that measured leg represents the current SLCI-01 baseline without
claiming any unmeasured xdist gain.

| Scenario | PR | Main push | Runner-minutes per merge |
| --- | ---: | ---: | ---: |
| Historical completed three-Python runs | 138.65 | 139.57 | 278.22 |
| Current main / SLCI-01 coverage, modeled | 43.90 | 139.57 | 183.47 |
| This PR, modeled, code change | 43.90 | 0.43 | **44.32** |

Main's 0.43 is its measured 3.12 job residual after subtracting the full
clone-proof and sandbox-provision steps (0.4146 minutes), plus the existing
smoke's locally measured 0.637 seconds. Checkout, installation, doctor, lint,
report/site checks and all publication gates remain. No cache, xdist,
documentation-filter or faster-test savings are credited in this estimate.
The incremental reduction from current main is **75.8%**, approximately
139.14 runner-minutes per ordinary merge with one PR attempt.

There were 18 merges in 30 days, or 4.2/week. At that cadence, with one PR
attempt per merge, one weekly schedule at its measured 143.76 minutes, and
the observed Pages frequency (0.074 minutes/week), current-main coverage
models **914.39 → 329.99 runner-minutes/week**. The actually observed
30-day spend normalizes to 1,777.76/week; that includes older matrices,
extra PR pushes, failures and cancellations, so it is not the same scenario.
Additional PR attempts add their actual job costs; each manual dispatch
still has six jobs (no observed dispatch sample to price independently).

The next five merged PRs have a target of **at most 50 total runner-minutes
per merge**, summing all associated PR attempts and the main push, including
cancelled attempts. This is an observation target, not a new CI gate.
Hosted results must verify it; hardware, publication-battery runtime and
reruns can change the result. The six-job schedule is the largest remaining
weekly cost in the model. Full PR clone proofs remain the largest per-merge cost.

## Coverage and optimization choices

- Code PRs retain the entire Python 3.12 Linux installed offline suite,
  publication battery, network containment and mandatory sandbox tests.
  No test was deleted, weakened or given a new skip marker.
- Main no longer repeats the full suite or its publication fixture battery.
  It runs the existing `scripts/smoke.py` instead: all three declared promises
  passed locally (fixture matrix, durable resume, provider-wall deferral).
  Publication hardcode/history, secret and licence gates remain on PRs and main.
  The full 3.11/3.12/3.13 × Linux/macOS matrix remains unconditional on weekly
  schedule and manual dispatch. No additional version coverage moves off PRs
  beyond SLCI-01's already merged choice. On main, Python 3.11 and 3.13
  regressions can therefore remain unseen until the weekly schedule. GitHub
  reports no classic protection for `main` and no matching branch rules as of
  2026-11-11; a PR validated against an older base can merge without a fresh
  full-suite run on the merged tree, leaving semantic conflicts undetected
  until that schedule.
- Documentation-only PRs omit the full clone proof (including its containment
  tests, sandbox provisioning and fixture battery), while keeping installation,
  doctor, lint, report integrity, site currency and publication gates. The exact
  allowlist is `docs/**/*.md` and `reports/**/*.{md,json,yaml,yml,png}`, including
  immediate children of those directories. Root Markdown, `site/`, config,
  catalogs, fixtures, tests, scripts, workflow and package changes all run the
  full suite. Unknown suffixes anywhere run it. Mixed changes run it.
  Empty/failed diffs run it. `git diff --no-renames` includes both sides of
  code-to-document renames and deleted code. There is no trigger path filter,
  so the existing check still completes on documentation PRs.
- `setup-python` caches pip on Standalone and Pages, keyed by `pyproject.toml`.
  The fresh clone receives only the pip artifact-cache path through its
  minimal environment. It still installs the actual cloned package into a
  new venv with separate HOME/state. The repeated install is retained as an
  installed-package proof; the measured outer install was only six seconds
  in the slowest job. Reusing a test/verifier venv would undermine isolation.
- The two slowest local tests spent 10.01 and 7.02 seconds resolving a
  submission with real sleeps before applying fake clocks. Three clock
  tests now submit through `search_domain` and resolve with patched clocks.
  Deadline, retry status and `[1, 2, 4]` backoff
  assertions remain; an exact clock/poll-count assertion was added.
  All three now pass together in 0.25 seconds, with each call phase printing
  0.00 seconds. Other domain-surface tests retain public `search_domain` coverage.

## Scheduled failures and local limitations

All four schedules had macOS clone-proof failures. The 12 macOS jobs consumed
5.67 raw runner-minutes total, or 1.42/week; their failing proof steps ended
before pytest. SLMACOS-01 owns their repair; this PR does not duplicate it.

The [2026-11-02 Linux 3.11 job](https://github.com/laceyenterprises/searchlight/actions/runs/41420434198/job/125194398039)
also failed 12 HTTPS cases of
`test_litellm_bridge_preserves_local_http_and_config`: curl/OpenSSL reported
`unexpected eof while reading` / exit 56. The other two Linux versions
failed the same suite step. This is separate from macOS; the Nov 9 Linux
jobs passed. It is named here without changing TLS/containment assertions.

Local profiling used the pytest fallback because the fresh-clone shell
requires a disposable hosted macOS firewall runner. A sterile HOME/environment,
standalone mode, plugin autoload disabled, four xdist workers and
`--durations=50` produced 4,535 passes, 273 existing skips, 44 failures and one
error in 175.78 seconds. It is a diagnostic profile, not an offline-proof pass.
Seventeen failures/error require the unprepared pinned wheelhouse; 24 local
proxy/sandbox cases failed on this host; harness-literal and state-inventory
checks also failed in unchanged sources. One workflow expectation failed
while the matrix was being updated and was corrected in the focused tests.
These host/baseline results are not presented as regressions fixed here.

Focused runner/repository/domain tests pass under Python 3.11, 3.12 and 3.13;
the existing OS-isolation-only test is skipped outside the offline runner.
The workflow matrix expressions are parsed and evaluated for all four events,
including unchanged event-separated concurrency. Shell syntax/shellcheck,
Ruff, repository lint, report checks and site currency are validated locally.

## Reproduction and five-merge observation

Use read-only `gh api --paginate` for
`repos/laceyenterprises/searchlight/actions/runs?created=>=2026-10-12T23:31:00Z&per_page=100`,
filter run creation timestamps to the half-open window above, then request
`repos/laceyenterprises/searchlight/actions/runs/<id>/jobs?filter=all&per_page=100`
for every run. Sum `max(0, completed_at - started_at)` across all jobs and
attempts, including failures/cancellations; unstarted jobs cost zero. Divide
seconds by 60. Classify by run event; do not add parallel job wall time only
once. Job logs are also available read-only through `actions/jobs/<id>/logs`.

For the next five merges after this PR, list merged pulls through
`repos/laceyenterprises/searchlight/pulls?state=closed&sort=updated&direction=desc&per_page=100`.
Match PR runs by PR number (and head branch/head commits when the API omits
closed-PR associations), and main pushes by `merge_commit_sha`. Include
every PR attempt, even superseded pushes, plus that main run. List Pages
separately and include it in overall weekly totals. Record each merge's total
against 50 minutes, and the scheduled/manual totals separately. Do not start
or rerun workflows to gather data. Repository settings and branch protection
remain operator decisions; neither changes in this PR.

## Local profile: thirty slowest phases before optimization

All rows below are call phases. These macOS timings describe the local
diagnostic sample, not Linux hosted timings or the publication battery.

| Rank | Seconds | Test |
| ---: | ---: | --- |
| 1 | 10.01 | `tests/test_domain_surfaces.py::test_async_poll_is_bounded_by_the_callers_wall_clock_budget` |
| 2 | 7.02 | `tests/test_domain_surfaces.py::test_result_fetch_backs_off_between_attempts_and_stays_inside_the_budget` |
| 3 | 6.45 | `tests/test_gap_workspace.py::test_requirements_setup_uses_clean_confined_offline_installer[empty]` |
| 4 | 6.11 | `tests/test_gap_workspace.py::test_live_workspace_bundle[pypi-codex]` |
| 5 | 5.97 | `tests/test_gap_workspace.py::test_live_workspace_bundle[shell-codex]` |
| 6 | 5.89 | `tests/test_gap_workspace.py::test_requirements_setup_uses_clean_confined_offline_installer[timeout]` |
| 7 | 5.81 | `tests/test_gap_workspace.py::test_backend_refusal_prevents_code_but_not_brief[bubblewrap unavailable]` |
| 8 | 5.37 | `tests/test_gap_workspace.py::test_live_workspace_bundle[None-claude-code]` |
| 9 | 5.32 | `tests/test_gap_workspace.py::test_bubblewrap_claude_refuses_without_host_srt_probe` |
| 10 | 5.31 | `tests/test_gap_verify.py::test_cell_and_verifier_use_same_pytest_version` |
| 11 | 5.11 | `tests/test_gap_workspace.py::test_live_workspace_bundle[None-codex]` |
| 12 | 5.03 | `tests/test_gap_workspace.py::test_live_canary_refuses_before_task[boot-failure-claude-code]` |
| 13 | 4.94 | `tests/test_gap_workspace.py::test_live_claude_boundary_receives_selected_broker_auth` |
| 14 | 4.87 | `tests/test_gap_workspace.py::test_live_canary_refuses_before_task[timeout-codex]` |
| 15 | 4.66 | `tests/test_gap_workspace.py::test_requirements_setup_uses_clean_confined_offline_installer[success]` |
| 16 | 4.55 | `tests/test_live_harness.py::test_harness_that_streams_forever_still_times_out` |
| 17 | 4.54 | `tests/test_gap_workspace.py::test_requirements_setup_uses_clean_confined_offline_installer[long]` |
| 18 | 4.44 | `tests/test_gap_workspace.py::test_backend_refusal_prevents_code_but_not_brief[Linux Claude code cells require broker OAuth auth]` |
| 19 | 4.42 | `tests/test_gap_workspace.py::test_requirements_setup_uses_clean_confined_offline_installer[exit]` |
| 20 | 4.36 | `tests/test_gap_workspace.py::test_live_workspace_bundle[hidden-claude-code]` |
| 21 | 4.06 | `tests/test_gap_workspace.py::test_backend_refusal_prevents_code_but_not_brief[bubblewrap canary denial unproven]` |
| 22 | 4.06 | `tests/test_live_harness.py::test_hung_task_is_killed_at_timeout_and_classified_timeout[claude-code]` |
| 23 | 4.05 | `tests/test_gap_workspace.py::test_live_codex_canary_retains_qualified_launch[True-False]` |
| 24 | 4.04 | `tests/test_live_harness.py::test_hung_task_is_killed_at_timeout_and_classified_timeout[codex]` |
| 25 | 4.04 | `tests/test_gap_workspace.py::test_live_workspace_bundle[pypi-claude-code]` |
| 26 | 4.02 | `tests/test_live_harness.py::test_a_straggler_that_ignores_sigterm_does_not_outlive_the_cell[straggler_hang-timeout]` |
| 27 | 3.86 | `tests/test_gap_workspace.py::test_live_workspace_bundle[diff-secret-codex]` |
| 28 | 3.77 | `tests/test_gap_workspace.py::test_per_cell_python_environment_is_offline[cell]` |
| 29 | 3.76 | `tests/test_gap_workspace.py::test_codex_offline_pip_with_prepared_cell_env` |
| 30 | 3.73 | `tests/test_gap_workspace.py::test_live_workspace_bundle[shell-claude-code]` |
