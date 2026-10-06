# GAPFRESH-01 code supply

Operator decision (2026-10-05): **"Stop Claude; mine fresher corpus".**
The GAP-13 Opus 5.5 calibration rejected all six attempted original code jobs
because their no-search floors passed (finding recorded by PR #7792). This
refresh adds 16 gap seeds and four stdlib controls. It retains every original
job; calibration and GAP-12 own admission and retirement.

The exclusive supply boundary is **2026-07-01**. On 2026-10-05, the official
[Opus 5.5 specifications](https://platform.claude.com/docs/en/models/opus-5-5/overview)
published June 2026 for both training and reliable knowledge cutoffs. The
[GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
published April 30, 2026 as its knowledge cutoff. July 1 is after both published
cutoffs; no retrieved evidence requires a later boundary. OpenAI's page does
not independently disclose the last training-example date, so this is a
published-cutoff supply policy, not proof that no example reached training.
Actual releases selected below are in August–October. Neither recency nor
validity proves an unknown API is outside a model's knowledge; full floor/ceiling
calibration remains necessary for both exact model IDs.

| Family | New jobs | Upstream changes |
| --- | ---: | --- |
| api-break | 4 | Stripe custom requests and resource positional parameters; urllib3 rewind exception; Click invalid-color exception |
| silent-default | 4 | Click lazy flag default/activation and help metadata; urllib3 instantiated queue resolution |
| release-breakage | 2 | Click deprecations break warnings-as-errors log and export adapters |
| vulnerable-dependency | 2 | PyJWT public-key material and empty symmetric JWK rejection |
| changed-third-party-api | 4 | Stripe checkout restrictions, country filtering, app installations and tax locations |
| control | 4 | Inventory aggregation, JSON Lines, repeated query values and final partial batches |

The gap seeds use Click 8.4.2 → 8.5.0 (August 26), PyJWT 2.13.0 → 2.14.0
(September 11), urllib3 2.7.0 → 2.8.0 (September 15), and Stripe 15.6.1 →
16.0.0 (October 1 PyPI upload; the changelog labels September 30). The event
rule uses the earliest non-yanked PyPI upload, exactly as GAP-06 does.
The receipt also contains Typer 0.27.0, whose metavar-only note was not used.

The mining records live under `sources/code-fresh/`. `mined.json` records the expanded GAP-06 pass.
`registry-and-notes.json` retains selected registry release metadata and verbatim
primary notes; it prunes unrelated registry releases and the historical Stripe
changelog below 16.0.0. PyJWT's release body only links the security changelog,
so its tagged primary changelog supplies the task-specific oracle. Each oracle
is a contiguous verbatim slice, with retrieval date and SHA256.
`control-oracles.json` records the pre-boundary Python documentation passages.

The default command was run first:

```bash
bin/hq-sew gap mine --ecosystem pypi --since 2026-07-01
```

It found urllib3 2.8.0. An expanded CLI pass stopped at a blocked Click docs
host. A source-recording call to the same `sew.gap.mine.mine` then examined
Stripe, Click, Typer, PyJWT, Requests, urllib3, HTTPX, Rich, Marshmallow,
Werkzeug, Django, Flask, SQLAlchemy, Pydantic, pandas, packaging, pytest,
attrs and Starlette. Its reader recorded unavailable sources as unavailable;
it did not invent notes. `read-failures.json` distinguishes allowlist denials
and SQLAlchemy's metadata-cap refusal from candidate rejection. Recorded
selected inputs replay offline through the unchanged miner. Missing reads
return None, and replay makes no network requests.

Every gap pin declares old/new/dependency roles and a wheel SHA256. The complete
closure is portable across supported Python 3.11, 3.12 and 3.14 on the macOS
validation host. Wheels remain outside git. GAPWHEEL-01 is present: agent cells
receive old/dependency wheels only; all roles are prepared outside the cell for
the verifier. Tests verify every new wheel's hash and dependency closure for
both author legs. Security fixtures use public synthetic test secrets, never
provider credentials or live identity data. Their hidden advisory and delivered
requirements assertions separately require the fixed PyJWT pin.

Stripe hidden tests replay synthetic schema-shaped responses through the real
pinned SDK services with the transport patched in process. They assert the
endpoint and wire parameters as well as successful adapter execution; no socket
or authenticated Stripe account is involved. The smoke checks invoke the same
business outcomes in a separate Python process, using the same synthetic
transport stubs. Controls exercise real incomplete stdlib jobs without bumps.
Visible tests check stable contracts, including deliberate no-op paths for
network adapters. Their coverage is small; it is not a substitute for the hidden
outcomes or ordinary grading checks.

Several tasks share releases, and two flag exporters share Click's lazy-value
change. Two new Stripe resource tasks are additions rather than migrations of
an existing resource. Some adapters can be repaired by a broad defensive change
without knowing the exact new identifier; these are supply candidates that may
still fail floor admission. Do not treat 16 tasks as 16 independent events or
quietly declare them admitted. Compare family coverage and per-release
correlation when calibrating, and retire decayed tasks through GAP-12.

## Worker checks and required operator receipt

The worker ran only targeted tests under this module's `tests/`. These establish
schema/source/asset/cache correctness, **not Seatbelt validity**. No live model
calls, provider reads, full worker-pool tests or generated indexes are involved.
The offline selection of fresh/corpus/catalog/miner tests passed 257 checks,
with 76 containment-dependent checks deselected. All 30 catalog wheels were
hash-verified offline. An additional run including `test_gap_validate.py`
reported 258 passes and five failures: the real validator could not apply
Seatbelt (`sandbox_apply: Operation not permitted`), and its control legs
could not run their visible tests. Those are infrastructure failures, not
accepted task-validation results. Do not bypass or mock the containment gate.
The final commit must be validated by the operator session before push/PR.
A receipt must include that exact SHA, host/containment evidence, wheel cache,
commands, results and any refused task. Validation cannot accept a missing
sandbox, failed infrastructure leg or skipped task.

From the exact commit on a Seatbelt-capable operator host:

```bash
cd modules/search-evaluation-workbench
bin/prepare-gap-code-wheelhouse --wheelhouse /path/to/gapfresh-cache --download
bin/prepare-gap-code-wheelhouse --wheelhouse /path/to/gapfresh-cache
export SEW_GAP_CODE_WHEELHOUSE=/path/to/gapfresh-cache
export PYTHONPATH="$PWD/lib/python${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pytest -q tests/test_gap_fresh_code_corpus.py

# Explicit validate-task receipts for each fresh gap and control.
python3 - <<'PY'
import subprocess
from sew.gap.catalog import load_gap_tasks
import os
for task in load_gap_tasks().values():
    if task['id'].startswith('gap-fresh-'):
        subprocess.run(['bin/hq-sew', 'gap', 'validate-task', task['id'],
                        '--wheelhouse', os.environ['SEW_GAP_CODE_WHEELHOUSE']],
                       check=True)
PY

# Separate validity legs, all repetitions, and ordinary grading regressions.
python3 -m pytest -q tests/test_gap_code_corpus.py -k every_gap_validity_triple_offline
python3 -m pytest -q tests/test_gap_code_corpus.py -k every_gap_ordinary_grading_offline
python3 -m pytest -q tests/test_gap_code_corpus.py -k every_no_change_control_offline
python3 -m pytest -q tests/test_gap_code_corpus.py -k security_code_only_repair
```

The targeted corpus commands cover both original and new jobs. The normal
validator owns three repetitions and stable hidden-failure reruns; direct
fixture pytest is not an accepted replacement. After an operator receipt,
push through the managed hook and open the single corpus PR. Post-merge,
prepare the cache and obtain the GAP-13 quota window to fully recalibrate
`claude-code@claude-opus-5-5` and `codex@gpt-6.1-sol` before any search battery.
