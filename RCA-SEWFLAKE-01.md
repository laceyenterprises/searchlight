# SEWFLAKE-01: interrupt tests depended on inherited SIGINT state


The 2026-10-04 GAP-12 validation at `1b622052db` ended with 2000 passed,
2 skipped and two live-runner interrupt tests failing with `DID NOT RAISE
KeyboardInterrupt`. Both passed in isolation and after the GAP-12 files.

The defect is reliance on the process's inherited SIGINT disposition.
`_thread.interrupt_main()` schedules the installed SIGINT handler; it does not
unconditionally raise `KeyboardInterrupt`. With `SIG_IGN`, it does nothing.
An asynchronous noninteractive Bash job inherits ignored SIGINT, Python retains
that disposition, and `env -i` does not reset it. The incident's validation
script uses `env -i` but does not normalize signals. This explains why changing
how the suite is launched can change the outcome without changing test order.
The culprit is the test session's uninitialized signal contract, not a runner
path swallowing `KeyboardInterrupt`.

## Investigation and evidence

At the worker's main baseline `430bece22d`, collect SEW tests in their ordinary
order and split the files before `test_live_runner.py` into successive halves:

- First half: 18 files, `test_agent_lane.py` through `test_evaluator.py`, followed
  by the entire live-runner file. Both interrupt tests passed. Overall 502
  passed and two unrelated provider-arm assertions failed.
- Second half: 19 files, `test_fixtures.py` through `test_live_harness.py`, followed
  by the entire live-runner file. Both interrupt tests passed. Overall 1135
  passed, 2 skipped, 32 failed and 34 errors in 497.17 seconds. The run omitted
  `SEW_GAP_CODE_WHEELHOUSE`, producing corpus setup errors; other failures
  were outside the interrupt tests (including offline verifier/containment
  expectations and the same provider-arm assertions). This diagnostic run is
  evidence about interrupt reproduction, not a green suite certification.
- Searching SEW found no parent-process `signal.signal` calls. The SIGTERM
  ignore in `test_live_harness.py` is script text executed in a child.
- A disposable interpreter explicitly starting with `SIG_IGN` reproduced both
  original `DID NOT RAISE` failures (2/2). A temporary diagnostic plugin shortened
  only the fake child's 600-second sleep to 0.5 seconds; neither assertion nor
  runner code changed. This avoids waiting for the ignored-interrupt hang to
  expire. These are deterministic signal-state failures, not a measured timing
  race.
- An actual asynchronous `/bin/bash` launch through `env -i` printed handler
  `1` (`SIG_IGN`), then returned normally from `_thread.interrupt_main()`.
- After the fix, both original tests, with their original sleeps, budgets and
  assertions, passed in an interpreter initialized with `SIG_IGN`: **20
  consecutive clean runs**, exercising both interrupt/resume cases each time.

The inherited-handler mechanism can be checked without running a suite:

```bash
bash -c 'env -i PATH="$PATH" python3 -c "import signal, _thread; print(signal.getsignal(signal.SIGINT)); _thread.interrupt_main(); print(\"ignored\")" & wait'
```

The incident log does not record `signal.getsignal(SIGINT)` or its outer
launcher. Inherited ignored SIGINT is a demonstrated sufficient mechanism and
fits the launch-dependent observations; attribution to the historical outer
Bash launcher is an inference, not a captured historical fact. No earlier
SEW test has been identified as leaking SIGINT. High host load alone has not
been demonstrated to cause these failures.

## Fix and guard

SEW's session fixture installs `signal.default_int_handler`, saving and restoring
the handler the embedding process supplied (or `SIG_DFL` when Python reports
`None` for a native handler that cannot be restored through `signal.signal`). A per-test autouse guard
reports a teardown error including the leaked ignored/custom handler and restores the
default before the next test. This preserves failure attribution and prevents
one offender from disabling all following interrupt tests. The CODEX_HOME
fixture explicitly depends on the guard to establish its surrounding lifetime.
Production runner signal behavior is unchanged.

Subprocess regression tests run only existing SEW test nodes, with plugins
isolated and a minimal environment. They verify both original interrupt tests
under inherited `SIG_IGN`, deliberately leak ignored and custom handlers,
assert the following test sees the default, and check the inherited handler is
restored after `pytest.main()` returns. The nested pytest invocation cuts off
ancestor conftests to avoid unrelated repository-wide plugin requirements; the
selected nodes must remain ledger-free because the root isolation plugin is
excluded. Nested runs start a separate process group and kill that entire group
on timeout, including sleeping fake-harness descendants.

## Validation

- `tests/test_signal_state.py`: 3 passed.
- Live-runner plus signal regressions, excluding the already-failing provider-arm
  assertion: 33 passed, 2 deselected.
- Entire live-runner plus regressions: 33 passed, 2 failed. The two provider-arm
  cases expect `provider_unavailable` when the fake harness never initializes
  its MCP server; baseline already returns `succeeded`. This mismatch predates
  this change and is independent of SIGINT. Assertions were left intact.
- File-order validation after `test_agent_lane.py`, `test_agent_os_config_helper.py`
  and `test_arms.py`, then live-runner and signal regressions: 130 passed,
  2 deselected (the baseline provider-arm failures).
- Only SEW tests were run; no worker-pool library suite, live provider, harness
  login or secret was used. The historical full suite was not rerun.
