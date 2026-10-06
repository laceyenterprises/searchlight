# Seed code corpus

GAP-07 adds 16 gap jobs and four no-change stdlib controls. These are authoring
seeds using the approved 2026-01-01 boundary. They are not yet admitted for any
model, and no calibration or provider battery is part of this change.

GAPFRESH-01 adds another 16 gap seeds and four controls with a 2026-07-01
supply boundary. See [CODE-CORPUS-FRESH.md](CODE-CORPUS-FRESH.md) for cutoff
evidence, separate source receipts, task balance and the operator validation
handoff. The original cohort and its receipts remain intact.

| Family | Jobs | Source behavior |
| --- | ---: | --- |
| api-break | 4 | Stripe resource rename, positional service parameters, removal of dictionary access on SDK objects |
| silent-default | 4 | Typer diagnostic locals, urllib3 queue resolution, Click default arbitration and enum flag inference |
| release-breakage | 2 | Click native-descriptor capture regression and Typer's vendored Click incompatibility |
| vulnerable-dependency | 2 | PyJWT critical-header validation and Requests ZIP extraction location |
| changed-third-party-api | 4 | Stripe mandate expansion, reserve expiry reason, checkout reuse state and bank-number state |
| control | 4 | CSV quoting, stable deduplication, UTC date normalization and path containment |

The mining receipts live in
`sources/code/`: the selected candidates plus recorded PyPI wheel metadata and
canonical release notes. Tests reproduce those candidate records using the real
miner without network access. All wheel bytes stay outside git. Dependencies use
universal wheels so the cache works across the supported Python versions.

Several jobs share an upstream release. The four third-party API jobs use small,
**synthetic**, locally recorded JSON responses conforming to the published
schema, not customer data or captures from a live authenticated Stripe account.
An in-process replay stub feeds those responses through the real pinned SDK;
no server socket is opened. These jobs cover different response contracts, but
shared-release correlation remains a limit for GAP-13 admission and reporting.

Hidden outcome tests and process-level smoke tests enter only the reconstructed
verifier workspace. The author reference is a fixture-only patch, including the
requirements migration. Security tasks check the installed dependency version
against offline advisory data and separately require the delivered requirements
to select the fixed catalog pin, as well as exercising the affected adapter.
No-change controls have no third-party dependencies and no requirements bump.

Ordinary grading always installs the catalog's new/dependency pins, after
validating any reconstructed requirements against the permitted pins. Keeping
the fixture's old requirements cannot avoid the target API break. Author
validation alone explicitly selects old/dependency pins for its old-visible leg.
The card-mandate contract returns `None` for a null or absent mandate field in
both dictionary and SDK responses, as well as identifiers for string and
expanded mandates.

Prepare the public wheel cache **before** offline validation (no provider keys,
1Password, or model calls):

```bash
modules/search-evaluation-workbench/bin/prepare-gap-code-wheelhouse \
  --wheelhouse /path/to/cache --download
```

Omit `--download` to check every required wheel hash offline. This preparation
helper never runs fixture code and does not establish task validity.

On a Seatbelt-capable macOS runner, run the mandatory corpus checks separately
by kind and then the full module suite:

```bash
cd modules/search-evaluation-workbench
export SEW_GAP_CODE_WHEELHOUSE=/path/to/cache
python3 -m pytest -q tests/test_gap_code_corpus.py -k every_gap_validity_triple_offline
python3 -m pytest -q tests/test_gap_code_corpus.py -k every_gap_ordinary_grading_offline
python3 -m pytest -q tests/test_gap_code_corpus.py -k every_no_change_control_offline
python3 -m pytest -q tests/test_gap_code_corpus.py tests/test_gap_catalog.py tests/test_gap_mine.py tests/test_gap_validate.py
python3 -m pytest -q tests
```

Each corpus test invokes the same `sew.gap.validate.validate_task` implementation
as `bin/hq-sew gap validate-task <task-id> --wheelhouse /path/to/cache`.
Three repetitions and hidden-failure reruns remain mandatory. A missing cache,
missing wheel, bad hash, or unavailable Seatbelt fails these tests; none is a
skip or accepted author receipt. CI must prepare/restore the wheel cache and use
a Seatbelt-capable runner before these offline commands. Operator validation
must target the exact final commit, not an earlier baseline capability receipt.
The ordinary-grading checks call `verify()` without role overrides for every
gap task: an empty diff fails hidden tests and the reference patch passes. They
complement the author-validity triples, whose forced roles cannot detect an
ordinary dependency-selection bypass.
Security regression cases also apply only the reference's code changes, retaining
the vulnerable requirements pin: grading must fail the deliverable-pin assertion
under the fixed target environment, while complete reference repairs pass.

Refresh authors should use [AUTHORING.md](AUTHORING.md). GAP-13 checks actual
model cutoff eligibility and floor/ceiling admission before running the pilot.

## Hidden-test repairs

Operator validation outside the worker sandbox found three hidden tests that
judged the wrong thing. All three were fixed without touching the validator's
acceptance predicates or the reference patches:

- **`gap-code-cli-capture`.** Click's fd capture redirects descriptors 1 and 2,
  while `sys.stdout.fileno()` refers to the saved original descriptor. The test
  now checks that fileno works and that native stdout written through
  descriptor 1 is captured. It used to expect a write to the saved descriptor to
  appear in the captured output.
- **`gap-code-cli-port-parser`, exit code.** An invalid port must produce a
  handled usage error: exit 2 with the invalid-port diagnostic. The test used to
  accept any nonzero exit, so the naive upgrade's uncaught external Click
  `BadParameter`, which Typer's vendored Click cannot handle, passed. The
  reference parser raises `ValueError`, which its own parameter type turns into
  a usage error.
- **`gap-code-cli-port-parser`, diagnostic text.** The test required the words
  "invalid port", which the reference cannot produce: Click reports a parser's
  `ValueError` as "Invalid value for '--port': <value>" and drops the
  exception's own text. The test now requires that usage diagnostic. Typer
  draws it in a Rich panel sized to the test runner's terminal, so the test
  compares words with box-drawing characters and line breaks removed. The
  prompt now says invalid input is "a command-line usage error", matching the
  exit-2 check, without hinting at the Typer change.

## Budgets

Code budgets stop runaway cells; they are not a spending target. Every code
task, gap or control, gets 2,000,000 tokens and 60 search calls. Wall-clock
limits are as authored: 1,200 seconds for gap tasks, 900 for controls. The
runner counts `total_billable` tokens, including cache reads, after the cell
ends. A cell over a limit is `budget_exhausted` and fails, so a cap set near
normal spend would turn cost into failures. Tokens per cell and per success are
reported beside the outcome instead.

The single-answer cells of the 2026-09-29 WSB batteries spent up to 320k tokens
on Claude Code and 574k on Codex, with up to 33 search calls. A code job adds
editing, test runs and shell turns on top. The briefs use 1,000,000 tokens and
60 calls for the same reason; see `BRIEF-CORPUS.md`.


### GAPWHEEL-01 wheel visibility and attempt evidence

Agent code cells receive only catalog package roles `old` and `dependency` in
their offline wheelhouse. `prepare_workspace(..., wheel_roles=...)` defaults to
those roles; the out-of-band verifier explicitly prepares `old`, `new` and
`dependency` outside the agent's readable paths. The existing read-only sandbox,
network denial and verifier qualification requirements remain in force.
`ArmAuditor.new_package_attempts` records heuristic observations of shell commands
naming a new package/version; it is audit evidence, not proof that every possible
acquisition command was detected. Spawn metadata persists that evidence under
`arm_audit.new_package_attempts`. Qualification refusal remains a verifier setup
refusal (`not_applicable`, `sandbox_unavailable`) rather than a patch failure.
