# GAP code task authoring template

Author `{candidate_id}` as a `{family}` code job from the recorded miner candidate
`{candidate_receipt}`. Use the primary source `{source_url}` and its release/event
date `{event_date}`. Preserve the cohort's recorded, operator-approved
**authoring-only boundary**: 2026-01-01 for original seeds, 2026-07-01 for
GAPFRESH-01 (see CODE-CORPUS-FRESH.md). Never rewrite older tasks' boundaries.
This is a supply boundary, not proof of eligibility for any model.
GAP-13 must check target-model cutoffs and calibrate before admitting these seeds.

Read the GAP spec and this catalog's CODE-CORPUS.md. Work only in SEW. Deliver an
ordinary engineering job, with a useful output contract, rather than a question
about release trivia. The prompt must not mention that a change happened, the
changed identifier, its replacement, or the target version. It may state what the
user needs the program to do. Preserve scope; do not tune for a search provider.

For a gap task:

1. Replay `sew.gap.mine.mine` on recorded public registry and release-note inputs.
   Retain the candidate receipt, release-specific notes, old/new universal wheel
   URLs and SHA256 pins. Verify that the release postdates the authoring boundary.
   False marker hits in release tooling are not application changes.
2. Put only the editable repository, ordinary visible tests, and dependency pins
   in `fixtures/<task-id>/`. Visible tests should exercise unchanged contracts
   that pass on both versions; keep the knowledge-dependent contract hidden.
3. Put hidden outcome tests, a process-level application smoke test, and
   `reference.diff` in `hidden/<task-id>/`. The patch must apply to the pristine
   fixture and migrate requirements as well as code. Never copy these into the
   fixture. Security jobs include an offline advisory check, an assertion that
   delivered requirements select the fixed catalog pin, and behavioral tests;
   API jobs replay schema-faithful recorded responses through an in-process stub
   because the verifier denies sockets. Mark synthetic payloads as synthetic.
4. Copy a contiguous, verbatim primary-source passage, at most 400 words, into the
   oracle. Record URL, retrieval date, event date, and SHA256. Include enough of
   the passage to explain the migration. Declare its giveaway terms. Never
   paraphrase an excerpt or invent an event date or source receipt.
5. Pin the full dependency closure with portable wheels. Downloading is an
   explicit author preparation step outside cells; validity execution is offline.
6. Run `hq-sew gap validate-task <task-id> --wheelhouse <prepared-cache>` under
   real Seatbelt. All three repetitions must show old-visible pass, naive-bump
   hidden failure with a stable rerun, and reference visible/hidden/smoke pass.
   Report infrastructure failures as failures, never as task defects. Do not
   replace the verifier with direct pytest, mock it, or skip containment.
7. Also exercise ordinary `verify()` without `install_roles`: an empty diff must
   fail a hidden test and the reference patch must pass. Grading installs the
   catalog's new/dependency pins even when fixture requirements still select
   the old version; author-only role overrides must not hide grading bypasses.
   For security jobs, a code-only repair retaining the vulnerable requirements
   must fail the deliverable-pin assertion under the fixed grading environment.

For a no-change control, use ordinary pre-boundary knowledge and a real unfinished
job: the unmodified fixture must pass visible tests and reproducibly fail a hidden
outcome test. Its verifier-side patch must pass visible, hidden and smoke tests in
all three repetitions. Use dependency-only pins, or no pins for stdlib tasks.
There is no version bump and no old/new requirement.

Keep each refresh cohort's mining receipts separate and replay them offline.
Add the manifest to tasks.yaml and the corpus tests. Assert all five code-family
counts (at least two each), at least 16 gap jobs and four controls. Keep distinct
`old-visible/naive-bump/reference` and `unmodified/reference` assertions. Add no
model calls, calibration receipts, provider secrets, binaries, or generated
indexes. Run targeted module tests permitted by the dispatch before managed push.
If this worker cannot apply Seatbelt, identify the exact commit and offline
commands for the approved operator validation lane and pause before PR closeout.

The reviewer pitch must explain the job, the real upstream change, the observed
validity legs, dependency/cache provenance, and limits of shared-release tasks.
Author validity is not model calibration; GAP-13 owns the latter.
