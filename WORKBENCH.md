# Search Evaluation Workbench

The distribution is `searchlight`; both `searchlight` and `sew` invoke the same
CLI. Install this tree with `python3 -m pip install ".[test]"`, then run
`SEW_MODE=standalone sew doctor` and `SEW_MODE=standalone sew validate-fixtures`.
The wheel includes the catalogs, legacy tasks, fixtures and metering configuration.
Agent OS installs the optional `[agent-os]` extra against its App SDK;
`bin/hq-sew` remains the source-tree wrapper for `hq sew`.

For extraction, run Agent OS’s `scripts/export-sew --commit <commit> --output <directory>`.
It exports only committed files, keeps tests and corpora, and records the resolved
commit in `PROVENANCE.md`. The output directory must be absent or empty.

To run a production task live, set `SEW_HARNESS_LIVE=1` and run
`bin/hq-sew run-live-harness --harness codex --task-id list-build-python313-pep594-removals`.
The task prompt, deliverable contract, and capped budgets come from
`catalogs/production/tasks.yaml`; the run bundle records `task_source: production`.
Production tasks refuse a different `--prompt-text` so their recorded source,
grading contract, and catalog spend caps stay aligned. An unknown task id with
an explicit prompt remains available for ad-hoc live smoke runs. A malformed
known task manifest fails closed, including when a prompt is supplied.

GAP code cells can opt into the [workspace driver contract](lib/python/sew/gap/WORKSPACE.md):
fresh fixture copies, offline virtual environments, editing and shell tools,
fail-closed egress canaries, contamination audits and a captured source diff.
The existing production lane keeps its retrieval-only tool surfaces.

Live provider arms in generic `hq-sew run` suites and GAP batteries separate
provider availability from metering observation. Each generated
`<run-dir>/provider-calls/availability.json` records `wrapper_engaged`, `observed`
and `observation_reason` alongside discovery flags, separately from billable calls.
`wrapper_engaged` means the parent installed the wrapper, before CLI boot;
`observed` means the child meter actually engaged. Observed discovery requires
successful MCP initialization and a harness "tools/list" response with at least
one tool; failed or empty discovery yields `provider_unavailable` with failure
category `provider_tools_unavailable`. Successful discovery with zero calls
remains gradable. Completed matching Codex MCP items or paired non-error Claude
tool results prove availability even when the snapshot could not be updated.
An engaged but unlaunched wrapper (`server_not_launched`) without completed calls
is unavailable only after harness startup is established by a succeeded terminal
status or a first-output marker. Before startup, missing discovery stays
unknown, preserving boot failures and pre-ready failures/timeouts in pass rates.
Other unobserved sessions (`url_transport`, `mcp_metering_core_unavailable`, or
`wrapper_not_engaged`) without completed calls also stay unknown and graded.
The stored `availability` is a child-observed discovery snapshot (`available`,
`unavailable`, `unknown`); engaged but unobserved wrappers store `unknown`.
`run.json.provider_availability` saves the live verdict before artifact overflow
can trim evidence. `provider_available` prefers this saved verdict for GAP,
adoption and reports; legacy bundles use the shared terminal filter, keeping
harness auth/spawn failures unknown. Child snapshot-write failures emit a stderr
sentinel and a sticky `<run-dir>/artifacts/meter-observation-failed.txt` marker outside the
snapshot directory; without completed calls they stay unknown and graded.
Inspect `unknown_n` alongside pass rates.
Crash-recovery adoption applies the same gate as fresh runs. GAP and bake-off
reports expose metering coverage separately; transcript evidence cannot recover
missing vendor spend. Use a stdio bridge and install the metering core to obtain
observation evidence. See [RCA-METERAVAIL-01.md](RCA-METERAVAIL-01.md),
[RUNBOOK-gap.md](RUNBOOK-gap.md), and the
[store contract](../../docs/data-model/18-peripheral-service-stores.md#search-evaluation-workbenchsewruns)
for evidence fields and attempt history.
MCP child stderr sentinels protect availability only when the harness forwards
that stderr. If both evidence directories are unwritable and stderr is hidden,
a stale parent snapshot can still misclassify discovery. Ready events alone do
not prove launch; first output reduces but does not eliminate asynchronous startup
races. Discovery snapshots remain `unknown` until tools/list finishes or a
handshake error is observed.


The [seed code corpus](catalogs/gap/CODE-CORPUS.md) contains 16 gap jobs across
five families and four no-change controls, with an offline wheel-cache preparation
helper and separate validity assertions for each kind. Refresh authors use the
[authoring template](catalogs/gap/AUTHORING.md). These seeds use the approved
2026-01-01 authoring boundary; GAP-13 owns target-model eligibility and calibration.

Author candidate jobs with `bin/hq-sew gap mine --ecosystem pypi --since <cutoff>`.
The miner ranks marked major/minor releases, records old/new wheel URLs and
hashes, and keeps a release-specific verbatim oracle with source/date/hash
receipts. It does not author fixtures or call a model. Use recorded sources in
tests; live mining reads public PyPI and release-note metadata.
For repeated mining, supply an OAuth-backed GitHub token through `GH_TOKEN`
(preferred) or `GITHUB_TOKEN` to use authenticated API quotas. The miner sends
the token only to `https://api.github.com` on the default HTTPS port and never
forwards it on redirects. Without a token, requests remain unauthenticated.
Connection and response-body timeouts produce the CLI's handled source error.

Run `bin/hq-sew gap validate-task <task-id> --wheelhouse <offline-cache>` before
model calibration. Place the author reference patch at the catalog task's
hidden `reference.diff`. The wheelhouse is required for tasks with pinned packages;
omitting it records a verifier setup refusal before installation. Standard-library
controls with no packages may omit it. In fresh verifier sandboxes, three repetitions require
old visible tests to pass, a naive new-version bump to reproducibly fail hidden
tests, and the reference patch to pass both. Controls use pinned dependencies
or the standard library without a version bump. Missing, skipped, unstable or
unsuccessful hidden execution refuses acceptance. Candidate and validation
receipts live under the configured state root's `gap/candidates` and
`gap/validation`, outside the tracked module. Seatbelt and offline wheel/hash
checks remain mandatory; this author validity check does not establish model
knowledge-gap calibration or approve a provider run.

SEW starts with a strict catalog and fixture schema layer. This module owns:

- `catalogs/lighthouse/suite.yaml`: the initial suite manifest.
- `catalogs/domains/{claims,tasks}.yaml`: DSB domain-strength claim registry and task catalog.
- `catalogs/domains/entity_resolution.yaml`: DSB-05 run instances for the six
  `entity_resolution` task types — subjects, ground truth, and validator/judge bindings.
- `lib/python/sew/entity_resolution.py`: executable scoring for those instances.
- `catalogs/production/{tasks,rubrics}.yaml`: WSB production task catalog and
  its judge rubrics.
- `catalogs/domains/suites/<domain>.yaml`: runnable DSB instances for one domain -- the
  repository, the question, and the ground-truth references a correct answer must cite.
- `tasks/*/task.yaml`: task manifests, prompts, and blinded judge rubrics.
- `fixtures/runs/*`: redacted success and failure bundles for fixture mode.
- `config/pi-model-profiles.yaml`: configured Pi OSS model profiles and their
  telemetry/tooling contracts.
- `lib/python/sew/schema.py`: validators for suite manifests, task manifests,
  run records, metrics records, evaluation records, normalized sources,
  provider calls, Pi harness records, and evidence bundles.
- `lib/python/sew/providers.py`: capability-aware provider adapter skeletons
  for `exa`, `parallel-web`, `firecrawl`, and offline fixture replay.
- `lib/python/sew/production_catalog.py`: offline validation and publication
  readiness for the WSB production catalog.
- `lib/python/sew/production_scoring.py`: deterministic per-field scoring of
  production deliverables.
- `lib/python/sew/cost_model.py` + `config/price-table.yaml`: WSB-04 normalized
  vendor and model spend, and dollars per successful task.
- `lib/python/sew/bakeoff_report.py`: the WSB-09 comparative bakeoff report
  (completion, measured tokens, and dollars per success, with deltas against
  the declared controls).
- `lib/python/sew/bakeoff_bundle.py`: the WSB-10 reproducibility bundle, its
  verifier, the per-task explain view, and the published headline report.
- `lib/python/sew/domain_suites.py`: strict loader for the runnable domain
  suites, the per-instance/per-vendor surface matrix, and the live
  ground-truth re-resolver.
- `lib/python/sew/domain_validators.py`: deterministic validators for the
  `code_and_pr` suite.
- `lib/python/sew/judge.py`: the WSB-02 blinded judge for rubric-scored
  production deliverables.

Fixture validation is intentionally offline:

```bash
modules/search-evaluation-workbench/bin/hq-sew validate-fixtures \
  --fixture-root modules/search-evaluation-workbench/fixtures/runs
```

The DSB domain catalog gate is offline too:

```bash
modules/search-evaluation-workbench/bin/hq-sew validate-domain-catalog
```

## DSB-05 entity resolution suite

The task catalog declares what each `entity_resolution` task *type* measures; the DSB-05 suite
declares what a run actually asks and what counts as correct. Its gate is offline, and prints
which instances carry ground truth that DSB-07 must re-verify before a live publication run:

```bash
modules/search-evaluation-workbench/bin/hq-sew validate-entity-resolution-suite
```

Three properties are enforced rather than documented:

| Property | Where it bites |
|---|---|
| Canonical identifiers are checked as identifiers | A company answer is compared against a pinned SEC CIK and a registrable canonical domain, not a name. ORCID and ISNI are checked against their ISO 7064 mod 11-2 check digit, so a transposed digit fails instead of matching. Returning a *confusable* domain (a subsidiary's product domain, or an unrelated same-named registrant) is a distinct failure reason from an unrelated miss. |
| A refusal can be the success condition | The private-individual instance inverts the suite's polarity: `status: declined` with no personal data **passes**, and a confident identification fails whether or not it is plausible. Its subject is a synthetic composite — resolving a real private person in order to test whether an arm declines to would perform the harm to measure it. |
| An entity match is not accepted without its disambiguating evidence | Disambiguation instances require evidence from at least two distinct declared evidence classes, and require the returned entities to carry *distinct* canonical identifiers. Two same-named people sharing one identifier between them have not been told apart. |

No personal contact detail may appear anywhere in any answer, including on the instances an arm is
expected to answer: an otherwise-correct company answer carrying a phone number has still
collected personal contact data.

`score_entity_answer(instance, answer, judge=...)` scores one arm's answer for one instance and is
the seam DSB-07 calls per cell. A rubric-scored instance with no judge wired fails
`judge_unavailable` rather than passing silently.
So is the runnable-suite gate, which also prints the surface each vendor uses
for each task instance:

```bash
modules/search-evaluation-workbench/bin/hq-sew validate-domain-suite --surfaces
```

## DSB domain suites

A domain suite binds each task type in `catalogs/domains/tasks.yaml` to one
public repository and to the references a correct answer must cite. `code_and_pr`
ships first (DSB-03, hypothesis H1).

Three rules keep the suite from deciding H1 before a query runs, and all three
are enforced by the loader rather than by review:

- **Ground truth is resolvable.** Every reference records the repository and the
  number, tag or path it must resolve to, and the declared URL is re-parsed and
  checked against those fields offline. `verify-domain-ground-truth` re-resolves
  the same references against the live GitHub API. The loader holds the manifest
  to the same bar the scorer holds an arm to, so the recorded example URL is
  itself a scoreable answer: a blob reference declaring `line_range` must carry a
  line anchor, and both ends of that anchor must fall inside the declared bounds
  -- the same conditions `match_reference` rejects as `missing_line_anchor`,
  `line_out_of_range` and `line_span_too_broad`. `line_range` is blob-only; on a
  pull, issue, comment or release reference it is precision the scorer can never
  enforce, so the loader refuses it.
- **One repository per instance, and no vendor index was used to pick them.**
  A suite whose `selection_frame.vendor_indexes_consulted` is anything but
  `none` is refused.
- **An instance may widen a task's source policy, never narrow it.** The Pillow
  migration-guide instance has to add a docs host; an instance that dropped one
  would bind a policy-scoped vendor to a corpus chosen per task, and its miss
  would say nothing about its index.

Scoring is binary. A pull request number one away from the right one is a
different pull request: the check records `near_miss` so the report can show what
the arm found, and scores zero.

The live ground-truth pass is skip-safe and off by default:

```bash
SEW_GROUND_TRUTH_LIVE=1 SEW_GROUND_TRUTH_TOKEN="$GH_TOKEN" \
  modules/search-evaluation-workbench/bin/hq-sew verify-domain-ground-truth \
  --domain code_and_pr
```

It checks the declared `state` too, and the three values are distinct claims:
`merged` requires the pull request to be merged, `closed` requires it closed and
*not* merged, and `open` requires it still open. A reference whose pull request
moved on fails the live pass even though the number still resolves.

## DSB scorecards

`hq sew domains report <run.json>` writes JSON and Markdown scorecards under a
reports directory beside the run file. `hq sew domains explain
<scorecard-json> --hypothesis H1` prints one claim, both statistics, the
vendor surfaces, task wins and losses, and evidence links.

The run file has `run_id` and an `outcomes` array. Each outcome is one scored
task observation with `domain`, `provider_id`, `task_id`, boolean `successful`,
`surface_id`, boolean `generic_surface`, and nonempty `evidence_links`; it may
also carry a positive `repetition`. Evidence paths are relative to the run
file and rewritten relative to the generated reports. Every vendor must have
observations in every domain. Cells below the shared minimum sample count stay
visible but cannot decide a claimant verdict or enter its cross-domain baseline.

The WSB production catalog gate is offline too, and also prints what would
block a publication run:

```bash
modules/search-evaluation-workbench/bin/hq-sew validate-production-catalog
```

## DSB-06 GTM deterministic validators

`lib/python/sew/evaluator.py` scores the four GTM validator kinds —
`cited_firmographics`, `dated_buying_signals`, `public_role_holder`, and
`honest_empty_result`. Two contracts are load-bearing:

| Property | Where it bites |
|---|---|
| Scoring thresholds come from the task, never from the answer | `dated_buying_signals` reads its `enforce_window` bounds from `deterministic_validator.window_start` / `window_end` on the bound task. An answer that restates a wider window, or omits its own bounds, cannot widen the range its signals are scored against; a restatement that disagrees with the bound window fails `correct:answer_window_mismatch`. |
| An unbound window fails closed | The DSB-01 catalog declares `enforce_window: true` as an evaluator contract; DSB-03 through DSB-07 bind the concrete window before a live run. Evaluating with `enforce_window` set and no bound window fails `correct:window_bounds_unconfigured` instead of silently skipping the check. |

Omitting a field cannot buy an arm a pass on the check that field feeds:

- A missing `signals` array fails `correct:missing_signals`, and a missing
  `person_name` or `role` fails `correct:missing_person_name` /
  `correct:missing_role` rather than bypassing the grounding checks.
- An account or signal with no `company`/`name` fails `correct:missing_company`.
  The deliverable schema constrains `accounts` and `signals` to bare objects, so
  nothing upstream requires the field; silently skipping the grounding check on
  an omitted one would let a signal carrying only a date pass the validator.
- Presence is tested on the *normalized* value, so a whitespace-only field is an
  omission — `_normalized("  ")` is `""`, and `"" in corpus` is trivially true.
- A non-object element inside `accounts` or `signals` fails
  `correct:malformed_account` / `correct:malformed_signal`. Such an element
  keeps the list non-empty — clearing the `missing_accounts` / `missing_signals`
  guard — while carrying no field the per-element checks can read, so skipping
  it would let `{"accounts": ["Acme Corp"]}` score a clean pass.

An *empty* `signals` list still passes, because the deliverable schema expresses
an honest "no qualifying signal" result as an empty `signals` plus populated
`no_signal_accounts`. Omitted `window_start` / `window_end` bounds are the one
exception handled elsewhere: they are in the task manifest's
`expected_output_schema.required` list, so `_check_schema` already fails the
omission as `schema_valid:missing_required_field`, and the signals are still
scored against the task bounds either way.

Two grounding details the corpus depends on:

- Each source `title` / `snippet` stays its own haystack; the corpus is a list
  of normalized strings, never one joined blob. A joined corpus lets a
  fabricated entity ground itself on text spanning two unrelated results — the
  tail of one snippet plus the head of the next title.
- JSON-null `title` / `snippet` values contribute nothing. Stringifying a null
  into the literal token `"None"` would admit it to the allowed corpus and let
  an entity hallucinated as "None" pass `fail_fabricated_entities` and
  `require_current_role_evidence`.
- `required_fields` presence uses an explicit null/blank test, not falsiness, so
  a legitimate `employee_count: 0` or `is_public: false` counts as present
  instead of failing `correct:missing_firmographic_field`.

## WSB-02 blinded judge

Rubric-scored production tasks have no deterministic truth, so a judge model scores them.
`judge_deliverable(task, rubric, deliverable, judges=[...], arm=...)` is the seam a runner calls
per cell. Use `resolve_rubric(task, registry)` to get the rubric. Four properties are enforced:

| Property | How |
|---|---|
| The judge cannot tell which arm produced a deliverable | The payload builder takes no arm. It projects the deliverable onto the brief's schema (tool traces and provider-shaped extras are dropped). It replaces every URL and hostname with an opaque `[source-N]` that keeps only the URL's shape (`commit_pinned`, `issue_or_pr`, ...), and collapses any run containing a provider, harness, model or tool name to `[redacted]`. Identical deliverables therefore produce byte-identical payloads under every arm label. `payload_sha256` on each record proves it. |
| A leak fails closed | After building the payload, `find_identity_leaks` searches it for the arm's own identifiers and any surviving URL or hostname. A hit records `blinding_failed`, and no judge is called. |
| Every rubric dimension is scored separately | A judge must return an integer score and a non-empty reason for every dimension. A holistic-only answer is `judge_invalid_result`. One dimension below the minimum fails the deliverable whatever the mean. |
| Disagreement is reported, not averaged | The first judge decides. Later judges measure agreement: per-dimension spread, verdict agreement and `verdict_disputed`. `summarize_agreement(records)` pools a run's records into exact, within-one and verdict agreement plus quadratic-weighted kappa. A record judged once says `not_measured`. |

The redaction vocabulary is fixed and applies to every arm. So `parallel` is redacted even when
it is only an English word. Redacting it for one arm alone would itself reveal that arm. Judges
are plain callables, and tests stub them; nothing in this module opens a network connection.

Harness driver fixture smoke runs are offline too. They write distinct Codex
and Claude Code native/external-provider evidence bundles:

```bash
modules/search-evaluation-workbench/bin/hq-sew run-fixture-harnesses \
  --output-root "$HQ_ROOT/tmp/sew-03-evidence"
```

### Live harness driver (WSB-05)

`sew.harness.run_harness` dispatches on `HarnessRunConfig.mode`. Fixture mode
is unchanged and stays the only CI path. Live mode (`sew/live_harness.py`)
really spawns the harness, and refuses to spawn anything unless
`SEW_HARNESS_LIVE=1` is set:

```bash
SEW_HARNESS_LIVE=1 modules/search-evaluation-workbench/bin/hq-sew run-live-harness \
  --harness claude-code --output-root /tmp/sew-live \
  --prompt-text 'Reply with only this JSON object: {"answer": "ok"}' --timeout-seconds 180
```

- **Spawn.** The argv matches the fleet adapters: `claude --print --output-format
  stream-json --verbose --no-session-persistence`, and `codex exec --json
  --ephemeral --skip-git-repo-check --sandbox read-only -o <file> -`. The prompt
  goes in on stdin. The cwd is a fresh scratch directory, so the repo's
  CLAUDE.md is not loaded. `SEW_CLAUDE_CODE_BIN` and `SEW_CODEX_BIN` override
  the binaries.
- **Environment.** The child gets an allowlist (PATH, HOME, locale, proxy/CA,
  and harness auth such as `ANTHROPIC_AUTH_TOKEN`/`CODEX_HOME`) plus
  `HarnessRunConfig.env`, never the caller's whole environment. The evidence
  records variable names only.
- **Limits.** The wall clock comes from the config, then the task manifest's
  `wall_clock_seconds`, then 600s. The boot deadline (no ready event yet)
  defaults to 120s. Token and provider-call caps apply only when the caller
  passes `max_total_tokens` / `max_provider_calls`; the suite runner's live
  executor (below) is what passes a task's manifest budgets.
  Timeout, cancel (`cancel_event`), and budget kills signal the harness's whole
  process group, so its MCP servers and tool subprocesses die with it.
- **Terminal statuses.** Each run ends in exactly one of `succeeded`,
  `failed` (it booted and ran but did not deliver), `timeout`, `cancelled`,
  `harness_boot_failed` (spawn error, no first output, exit before ready, or
  expired/invalid auth), `provider_unavailable` (429/529/quota/overload/
  transport), or `budget_exhausted`. `failure_category` carries the finer
  reason. A boot failure is never folded into `failed`, which is the status
  read as task quality. The runner still retries `harness_boot_failed` as it
  retried boot failures before.
- **Evidence.** `transcript.json` keeps every harness event whole under
  `harness_event` so WSB-06 can re-check tool calls, and adds the
  `prompt_sent`/`harness_ready`/`first_output_token`/`final_answer` markers
  that `metrics.py` reads for latency. Credentials are scrubbed with the fleet's
  HRR-09 vocabulary (`agent_os_core.provider_errors`), plus bearer and cookie
  headers. Long strings are shortened, never dropped, to fit the 200 KB artifact
  cap. Usage is recorded as disjoint input / cached_input / output / reasoning
  buckets that sum to `total_billable`, so budget summation never
  double-counts cache reads or thinking tokens. Claude cache-write counts and
  any 5-minute/1-hour split are retained as metadata within `input` for pricing.
- **Not here.** Tool exposure per arm is WSB-06's job, through
  `harness_args` and `env`. Grading is also out of scope: the evaluation record
  is left `pending` for the evaluator and judge.

### Live suite execution (WSB-07)

`hq-sew run --mode live` runs a suite's matrix through `LiveCellExecutor`,
using the runner's existing seeded order, budget tracker, and resumable state.
It still refuses to start without all four `--max-*` operator budgets:

```bash
SEW_HARNESS_LIVE=1 modules/search-evaluation-workbench/bin/hq-sew run \
  --suite <suite> --mode live --provider-mcp-config ~/sew-mcp.yaml \
  --max-provider-calls 400 --max-provider-result-chars 2000000 \
  --max-total-tokens 5000000 --max-wall-clock-seconds 14400
```

- **Budget precedence.** The suite manifest's `budgets` are committed spend
  ceilings; each `--max-*` spend cap applies only where it is lower. The run's
  wall clock is `--max-wall-clock-seconds` as given; the suite's
  `timeouts.run_seconds` is the wall clock only for a run without operator caps.
  Per-cell limits come from each task's catalog `budgets`. The run result lists
  the limits that applied as `budget_limits`. Elapsed time persists across
  resumes: after `wall_clock_budget_exhausted`, resume with a larger cap.

- **Preflight.** Before any state is written or any cell spawns, the run is
  refused if `SEW_HARNESS_LIVE=1` is unset, a harness binary is missing, a
  provider arm has no MCP server, a hosted harness names a `model_profile`
  other than `default`, or an arm's spawn surface fails the WSB-06 contract.
- **Provider arms.** `--provider-mcp-config` maps a provider id to the MCP
  server its arm exposes (`exa: {command: npx, args: [...], env: {...}}`).
  `${NAME}` values are read from the environment at load, so the file holds
  references, not keys; an unset reference refuses the run. In `args`, only
  non-secret `URL`/`ENDPOINT`/`HOST`/`PATH`/`DIR` names or suffixes are allowed,
  and credential-shaped values are refused after resolution. Keep credentials
  in `env`, or use `header_from_env: {name: Authorization, value: "Bearer ${KEY}"}`
  with a bridge supporting `--header-file` (verified in `mcp-remote@0.14.3`). SEW
  writes the header to a temporary 0600 file and passes only its path; see the
  [header-file pattern and cleanup contract](RUNBOOK-gap.md#environment-and-egress-canary).
- **Cost metering.** Each stdio provider server runs behind `sew.mcp_meter`,
  which relays the MCP stream unchanged (the agent still sees the vendor's own
  tools) and writes one provider-call record per tools/call request into the run's
  `provider-calls/`. Spend comes from the vendor's response when it reports
  one (Exa `costDollars`, Perplexity `usage.cost`, Tavily and Firecrawl
  credits, including Firecrawl Alexandria's inline `data.creditsCost`). Otherwise
  `config/mcp-meter-tariffs.yaml` maps the tool to a
  published price-table tier or credit rule, and otherwise the call is recorded
  unpriced with a reason. A call the server never answered is recorded
  `failed` (`no_response`). A frame that is not JSON is still relayed; the
  meter counts and skips it. The relay and pricing come from the shared
  Agent OS core (`cwp_dispatch.mcp_metering`); a checkout that cannot import it
  runs every arm unmetered, logs `mcp_metering_core_unavailable` once, and
  never fails the run. A record never keeps free-text arguments (queries,
  URLs, prompts) or unexpected argument names: it keeps recognized argument
  names, a count of unexpected names, and the enum values the tariff prices by
  (such as `search_depth` or `search_type`). It keeps no digest of tool inputs
  or response text that a reader could match against guessed private content. An
  unrecognized enum is recorded by selector name only, with no supplied value.
  Tool identifiers in `operation`, `request.tool`, and `response.meter.tool` are
  kept only when they exactly match a tool in the provider's configured tariff;
  unknown or malformed names become `unknown` before record IDs are derived,
  including for failed calls. The proxy still forwards the original MCP request.
  Tavily search calls are unpriced when its MCP server's `DEFAULT_PARAMETERS`
  overrides `search_depth`, unless the response reports actual credit usage;
  that server setting replaces even an explicit depth in the tool call.
  Remote (`url`) servers cannot be wrapped and stay unmetered.
- **Pricing parity (VENDORPRICE-01).** `config/provider-arm-tools.yaml` captures
  the six battery arms' pinned MCP configurations and advertised tool names.
  Loading provider exposures checks each enabled tool against the tariffs;
  explicit selections (`enabled_tools`, Exa `ENABLED_TOOLS`/`TOOLS`, or the
  hosted Exa `tools` query parameter) replace the captured default list.
  Missing rules in these captured defaults and listed selection channels fail
  admission; unreadable or missing provider tariff registries have a distinct
  refusal. Other CLI/vendor environment selections and server-version drift
  are not inspected: set an explicit `enabled_tools` list for those configs.
  `disabled_tools` must be a list of tool names. The meter also counts uncovered
  names from live MCP tool discovery without publishing the names. GAP and bake-off
  JSON and Markdown report priced/recorded calls per vendor, estimated calls,
  missing tool rules, unobserved cells, and unpriced reasons independently of
  model-token accounting. Explicit unpriced rules document gaps rather than
  claiming parity of known dollar amounts.
  Exa's text-only MCP responses fall back to the published search/contents
  rates; `costDollars` still wins when present. Its stdio search default is
  `auto`, with `DEFAULT_SEARCH_TYPE` respected. Parallel search prices only an
  observable mode (call argument or documented connection override); the
  hosted tool's absent mode is `search_mode_not_observed`. Fetch prices count
  returned pages when structured results exist; text-only responses use the
  requested URL count as a visible estimate, which may overstate successes.
  Searches above the included ten results require an actual result count.
  See [historical cost recovery](RUNBOOK-gap.md#historical-cost-recovery-vendorprice-01).
- **Per-cell budgets.** Each cell runs under its own task manifest budgets:
  `wall_clock_seconds` is its timeout (`timeout`), and `max_total_tokens` and
  `max_provider_calls` end it as `budget_exhausted` (`token_budget_exceeded`,
  `provider_call_budget_exceeded`). Neither is `failed` or retried. Every token
  counts, cached input and boot context included, so retrieval-sized budgets
  such as the lighthouse suite's 8,000 exhaust on a harness's first turn; size
  a live suite's budgets for harness cells. Search calls are provider-MCP or
  native web tool calls read from the stream, counted once per call id. A cell
  that answers but whose final usage is over budget is also `budget_exhausted`,
  because Codex reports usage only at turn end.
- **Suite budgets.** A cell killed mid-stream has no closing usage row, and
  its metered provider-call records may be incomplete. The tracker therefore charges the
  larger of the metrics and the driver's own process meters, so the cells that
  overspend are not the ones the suite budget treats as free. If a live cell is
  interrupted before the full bundle is written, the partial directory keeps an
  `<cell-dir>/artifacts/provisional-meters.json` snapshot and resume charges those calls
  and tokens before starting another cell. `--max-provider-result-chars` has no
  live meter yet. The run wall clock is checked between cells and its elapsed
  value is persisted in runner state, so resume continues the same run clock
  instead of starting a fresh invocation budget.
- **Resume.** The run id and state are separate from a fixture run of the same
  suite, and a fixture run cannot be resumed in live mode. Ctrl-C mid-cell
  kills the harness's process group and records no terminal outcome for that
  cell; resume first charges the partial directory's provisional meters, then
  reruns it under a fresh `-rN` id only if suite budgets still permit another
  attempt. A cell whose bundle was written before a crash could index it is
  adopted on resume only after the normal fixture validation, live run identity
  check, and final evidence secret scan pass, so a bundle the live writer
  rejected for secret-like evidence cannot enter the index. A
  `provider_unavailable` bundle is never adopted, because it holds no usable
  answer. Pi has no live cell path, so its cells record `not_applicable`
  (`live_harness_unsupported`).
- **Provider-unavailable streaks.** `provider_unavailable` is not retried. One
  such cell is recorded and the suite continues. Three in a row
  (`PROVIDER_UNAVAILABLE_STOP_STREAK`) read as a shared wall, such as the
  harness account 429 of 2026-09-29. The runner then takes those cells back out
  of the index and stops with `stopped_reason: provider_unavailable`. When the
  same cell also used up a suite budget, the budget reason is reported instead.
  The summary's `deferred_unavailable_cells` says how many of the remaining
  cells wait on the wall. Resume the same run id once the wall clears: the
  deferred cells run again under the next free `-rN` id.
  `runner-state.deferred_unavailable` records each still-deferred cell's count
  and walled bundles; a record is removed once its cell is indexed. The cell's
  index entry lists those bundles first in `attempt_run_dirs` and carries
  `unavailable_deferrals`, so the evidence stays with the cell and the runner's
  suite budget charges its spend. The bakeoff report's cost figures read only
  the entry's `run_dir`, so they leave out walled attempts. A
  `provider_unavailable` bundle a crash left unindexed is listed the same way
  when its cell re-runs. A streak carries across calls: cells a crash or an
  interrupt left at the index tail still count, and the next unavailable cell
  defers them all. A
  cell deferred `PROVIDER_UNAVAILABLE_MAX_DEFERRALS` (2) times is recorded as
  `provider_unavailable` on its next unavailable run and does not count toward
  a streak. A wall on a single arm (one account's usage limit) therefore stops
  a bounded number of resumes, and then the rest of the matrix finishes. The
  count is per cell and cannot tell one walled arm from a suite-wide wall, so
  resuming while the wall is still up costs cells: each early resume can make
  a streak's cells terminal. The summary's `unavailable_exhausted_cells` lists
  them; rerunning them needs `--no-resume`.

Pi fixture runs are also offline and produce normal SEW run bundles:

```bash
modules/search-evaluation-workbench/bin/hq-sew pi-fixture \
  --output-root /tmp/sew-pi-fixtures \
  --profile oss-small \
  --profile oss-large
```

The optional live smoke is skip-safe unless `SEW_PI_LIVE=1`, a Pi runtime, and
the selected provider credential are present:

```bash
SEW_PI_LIVE=1 modules/search-evaluation-workbench/bin/hq-sew pi-live-smoke \
  --output-root /tmp/sew-pi-live \
  --profile oss-small \
  --provider exa
```

State defaults resolve through `agent_os_config.roots` to `$HQ_ROOT/var/sew`.
Use `SEW_STATE_ROOT` only for explicit disposable local overrides.

Provider live smokes are skipped unless `SEW_ALLOW_LIVE_PROVIDER_SMOKE=1` is
set. Credentials must come from explicit operator environment values or
configured references:

- `SEW_EXA_API_KEY` or `SEW_EXA_API_KEY_REF`
- `SEW_PARALLEL_WEB_API_KEY` or `SEW_PARALLEL_WEB_API_KEY_REF`
- `SEW_FIRECRAWL_API_KEY` or `SEW_FIRECRAWL_API_KEY_REF`
- `SEW_BRAVE_API_KEY` or `SEW_BRAVE_API_KEY_REF` -- the Brave **Search** key
  (`env:PROVIDER_API_KEY`). Brave keys are
  plan-scoped: the Answers key is refused by web search and vice versa.
- `SEW_TAVILY_API_KEY` or `SEW_TAVILY_API_KEY_REF`
  (`env:PROVIDER_API_KEY`)
- `SEW_BRAVE_ANSWERS_API_KEY` or `SEW_BRAVE_ANSWERS_API_KEY_REF`
  (`env:PROVIDER_API_KEY`) -- the agent lane's
  `brave-answers` arm; the Search key is refused by the Answers endpoint
- `SEW_PERPLEXITY_API_KEY` or `SEW_PERPLEXITY_API_KEY_REF`
  (`env:PROVIDER_API_KEY`) -- one key serves
  both the Search API adapter and the `perplexity-agent` arm

`*_REF` values may be `env:NAME` or `op://...`; `op://` resolution routes
through the Agent OS cached credential resolver when it is importable. Run it
with the host's service-account token exported and
`OP_BIOMETRIC_UNLOCK_ENABLED=false`, so an unattended run fails closed rather
than prompting for 1Password approval.

### Perplexity

Perplexity enters the workbench through two of its APIs, one per lane,
because the lanes measure different things:

- **Search API** (`POST /search`) is a retrieval provider beside the others:
  raw ranked results, no LLM answer. `mode: fast` sends the documented
  `search_type: fast` (\$1/1K requests vs \$5/1K for `web`), and
  `max_chars_per_result` becomes `max_tokens_per_page` at 4 chars/token.
  Spend is published, not returned, so `cost_usd` stays unset.
- **Agent API** (`POST /v1/agent`) is the `perplexity-agent` arm in the agent
  lane: a preset plus a `response_format` JSON schema, scored like the other
  vendor agents. `tools` is not sent -- the preset's own web tools would be
  replaced. `output_text` is an SDK convenience absent from the wire, so the
  arm assembles it from `message` content parts. Actual spend comes from
  `usage.cost.total_cost`. In structured mode Perplexity returns no URL
  annotations, so `citation_count` is `None`; retrieved results are recorded
  as `units.search_results_retrieved`, never counted as citations.
  Tiers take the deepest preset whose median cost (the pricing page's own
  per-preset figures) fits: `low` -> `medium` (~\$0.008), `mid` -> `high`
  (~\$0.065). Observed 2026-09-25: the presets ran on `openai/gpt-6-luna`
  although the pricing page lists `gpt-5.6-luna`; the arm records the model
  the response reports.

No SDK is used: every provider here shares the stdlib transport that the
offline tests inject, and the request shapes follow
docs.perplexity.ai/api-reference/{search-post,agent-post}.

The agent lane's POST helper honours `Retry-After` on a 429 as a floor under
its own backoff; a `Retry-After` over 60s is longer than a cell can wait, so
the request stops at once and the cell records the 429 instead of re-hitting a
refused window. Run polls use a socket timeout bounded by the cell deadline
(at most 60s). The Agent endpoint reported
`x-ratelimit-limit: 1` on this account.

### Brave Answers and Tavily Research (agent lane)

- `brave-answers` calls `POST /res/v1/chat/completions` in blocking
  single-search mode. It has no structured-output parameter, so the schema is
  put in the prompt and the reply parsed (a code fence is tolerated; prose is
  kept as text and fails the schema check honestly). Blocking mode offers no
  citations, so `citation_count` is `None`. Spend is computed from the
  reported tokens at the published \$4/1K requests + \$5/1M tokens. Research
  mode requires SSE streaming and is not wired yet; `mid` widens
  `search_context_size` instead.
- `tavily-research` submits `POST /research` and polls
  `GET /research/{request_id}`. Tavily accepts only `properties` and
  `required` at the top of `output_schema` (a top-level `type` is a 400), so
  the task schema is reduced to those. `sources` are the report's citations.
  Pricing is dynamic per request (mini 4-110 credits, pro 15-250) and no spend
  is returned, so `cost_usd` stays unset and the tier list price is the
  documented floor.

Brave and Tavily are retrieval providers in every lane that enumerates them
(retrieval tiers, lighthouse matrix, DSB domain suites). Neither makes a domain
claim or ships a domain surface, so in DSB they are generic baselines in all
four domains. Brave's web search takes no depth selector (the adapter turns on
`extra_snippets`, its only excerpt knob); Tavily's `mode: fast` maps to its
`basic` depth and anything deeper to `advanced`.

Downstream tickets consume this layer as their contract:

- Provider adapters write `provider-call` and `normalized-source` records.
  Live provider source IDs are namespaced under the call ID recorded for that
  request, and live HTTP transports read bounded response/error prefixes so one
  malformed provider payload cannot exhaust the worker process.
- Harness drivers write `run` records and evidence bundle artifacts.
- Pi harness records distinguish resolved OSS model names, tool-calling mode,
  provider-result compression, provider adapter exposure, fixture replay, and
  measured/estimated/unknown token accounting.
- Evaluators write `evaluation` records against task manifests and rubrics.
- Metrics aggregation writes `metrics` records without treating unknown tokens as
  zero.
- Runner and report tickets read the suite/task manifests and link reports back
  to redacted evidence bundles.

### Async provider runs (Parallel FindAll)

Some provider surfaces acknowledge a submitted run instead of returning
results. `HttpProvider.resolve_async_run` polls the vendor's status endpoint
and then fetches the result, so a FindAll cell returns retrieved sources like
any other cell instead of terminating on the acknowledgement.

Operator-facing consequences:

- **One cell is many round trips.** The submission POST plus every poll GET is
  counted in the call record's `response.async_poll_calls`; size
  `max_provider_calls` and the task's `wall_clock_seconds` against that, not
  against one request.
- **The poll is bounded by wall clock, not just attempts.**
  `ASYNC_POLL_BUDGET_SECONDS` (default 300s) is the horizon for the whole
  submit-then-poll run; `timeout_seconds` continues to bound each individual
  GET. A request may override the horizon with `poll_budget_seconds`.
- **Transient failures are absorbed, not fatal.** A 408/429/5xx or a socket
  timeout on one status GET is retried at the normal poll cadence, and the
  final result fetch gets its own bounded retry with exponential backoff
  (`ASYNC_RESULT_MAX_ATTEMPTS`, `ASYNC_RETRY_BACKOFF_SECONDS`). Retries never
  extend the wall-clock horizon. Every other 4xx — 401, 403, 404 — stops
  immediately, because asking again returns the same answer.
- **The failure reason is on the record.** A stopped poll keeps the pending
  receipt but replaces its `error_class`:
  `async_poll_http_<code>` / `async_poll_timeout` (could not determine whether
  the run finished), `async_result_http_<code>` / `async_result_timeout` (the
  run finished but its result could not be collected),
  `async_poll_deadline_exceeded` (the horizon expired — the transport failure
  being retried at the time, if any, is recorded in
  `response.async_poll_last_error`), `async_run_not_completed` (the vendor
  reported a settled non-success state, shaped into
  `response.async_observed_status`), `async_run_pending` (genuinely still
  running at the attempt cap), `async_run_handle_malformed`, and
  `async_poll_disabled`.

## Retrieval lane (live provider comparison)

The retrieval lane compares Exa, Parallel Web, and Firecrawl on the raw search
surface: one HTTP round trip per cell, no agent in the loop. Separating this
from the harness matrix is deliberate — mixing provider effects with model
effects makes neither attributable, and single-round-trip cells are cheap enough
to repeat until the error bars are meaningful.

```bash
# Credentials resolve from the secrets bus; see runtime/config/op.env.
modules/search-evaluation-workbench/bin/hq-sew retrieval \
  --config matched --repetitions 3
```

Two configs ship, and the difference between them is the point:

| config | what it pins | what it answers |
|---|---|---|
| `matched` | every provider on a comparable fast tier, common excerpt budget | which index is better, holding retrieval depth constant |
| `default` | each vendor's own quickstart shape | what a developer actually gets without tuning |

**Never compare library defaults and call it a latency result.** Parallel
defaults to `advanced` (~3s); Exa's default `auto` is a fast path. A table built
on defaults measures configuration choices, not capability. `matched` exists so
that ranking quality and latency can be read independently of vendor defaults.

### What is scored

| signal | meaning |
|---|---|
| `answer_rate` | the returned content contains the verified answer, so an agent could answer with no follow-up fetch. Reported with a 95% Wilson interval. |
| `gold_hit_rate` / `gold_mrr` | whether the authoritative domain appeared, and at what rank — ranking quality, independent of how much text ships |
| `content_chars` | what the provider actually returned, measured before the evidence-bundle excerpt cap, because this is the agent's context cost |
| `latency_ms` | client-observed wall time, which is what an agent waits on |
| `provider_cost_usd` | only where the provider reports spend directly (Exa). Firecrawl reports credits whose dollar value is plan-dependent; Parallel reports neither on the search response. Both stay `null` rather than guessed. |

Queries and ground truth live in `catalogs/retrieval/queries.yaml`. A query that
*every* provider misses is reported as `suspect_ground_truth` and excluded from
rates: hand-written answer patterns are likelier to be wrong than a simultaneous
three-way provider failure.

### Known measurement caveats

- Each cell builds a fresh HTTP client, so every provider pays TCP+TLS setup on
  every call. That is uniform across providers and mirrors an unpooled agent
  tool call, but absolute latency runs higher than a pooled client would show.
- Firecrawl exposes no retrieval-depth selector and no per-result character cap
  on search, so `matched` cannot equalize its content volume the way it does for
  the other two. That asymmetry is a property of the product, and is recorded
  rather than normalized away.
- Live web results drift. Reports are timestamped and every cell is written to
  `cells.jsonl` with its URLs, so any aggregate can be re-derived or disputed.

## WSB production task catalog

`catalogs/production/tasks.yaml` is the WSB task source. It replaces the L2
fact catalog for bakeoff runs; `catalogs/agent/tasks.yaml` stays in the tree
because the shipped agent lane still runs it.

The L2 catalog is why this one exists. Eight fact-shaped questions with
regex-checkable answers, and all three vendor arms scored 100% on every one. A
benchmark where everybody wins has measured nothing except that the floor is
low, and it cannot discriminate again when the products improve.

Eighteen tasks, three in each of six classes:

| class | the job |
|---|---|
| `list_build` | assemble N entities matching criteria, enriched per column |
| `change_detection` | diff two stated points and report what breaks, without reporting additive changes as breaking |
| `upstream_diagnosis` | given a concrete error, find the upstream change that explains it |
| `competitive_table` | build a cited comparison whose unpublished cells are marked rather than filled |
| `multi_hop_entity` | resolve entities, then facts about each, then a question needing all of them |
| `unanswerable` | the honest deliverable is that the evidence does not exist |

Every task declares a deliverable schema, a citation field, a freshness window,
per-task budgets for provider calls, wall clock and tokens, and exactly one
scoring instrument: per-field ground truth where truth is deterministic, a
judge rubric where it is not. Never neither, never both.

### Measurement headroom

Four tasks are `expected_outcome: expected_fail` — the catalog expects every
arm to fail them, each for a stated structural reason (the evidence must be
derived rather than retrieved; the honest deliverable is a refusal that a
retrieval-shaped objective is not rewarded for producing). Validation refuses a
catalog with fewer than three, or with them parked inside fewer than three
classes: the suite reports per class, so headroom concentrated in one class
leaves every other class free to saturate.

### Declining and fabricating are different findings

Per-field scoring keeps the two apart. A field the arm did not produce is
`missing` — it declined. A field it produced wrongly is `wrong` — it
fabricated. Both score zero. An arm that says "I could not establish this" and
an arm that invents a commit hash are not the same product, and the
expected-fail tasks exist to produce that distinction at scale.

### A structural check needs the structure

`member_set` rules named for a child field (`element_rules`,
`element_rules_all`) are claims about that child field, so a member that is a
bare string rather than an object has no subject for them and fails the field.
A deliverable that flattens the declared objects into prose bullets is not
scored against the whole bullet: a flat list of package names must not satisfy
`package` and `maintainer` at once while answering neither.

`table_cells` scores every cell against every rule that names it, and no cell
value opts out. A column whose figure may legitimately be unpublished says so
in `column_rules` (`any_of: ['^\s*unavailable\s*$']`), and a column no rule
names already accepts any non-empty answer — so the permissive case never
needs a magic string. The `forbid_numeric_columns` guard covers the opposite
direction, where an arm invents a figure for a cell the vendor withholds.

### Truth that can rot says so

`truth_provenance.status` is `immutable_history` (a closed historical record —
what a release removed, what a license clause says), `current_state` (a live
condition, which carries a note and a short window), or `none` (rubric-scored).
`revalidate_by` must equal `as_of` plus `window_days`, so a task cannot look
freshly windowed while its real deadline is years out.

`publication_blockers()` returns every task whose truth was asserted by the
catalog author rather than re-fetched, plus every live-condition task past its
`revalidate_by`. All 16 deterministic tasks are currently
`verification_method: author_assertion`: the values are asserted against the
named primary sources and have not been re-fetched, so a publication run
should re-verify them first rather than quote accuracy against them.
`validate-production-catalog` prints the list.

### Suspect ground truth

The L1/L2 convention is preserved. A field two or more independent arms all get
wrong is likelier bad ground truth than a simultaneous multi-arm failure, so it
is reported as `suspect_ground_truth` rather than booked as one miss per arm,
and repetitions are grouped by arm first so one noisy arm cannot look like
agreement. Expected-fail tasks are exempt: universal failure there is the
design, and letting the heuristic fire would delete the headroom.

### Fixtures

`fixtures/production/model-answers/` holds one worked deliverable per
deterministic task, each of which must score `passed: true`. The property is
satisfiability — a task whose truth no deliverable can satisfy fails every arm
for a reason that has nothing to do with the arm, and reads in the report as a
hard task. `fixtures/production/golden/` pins exact scoring records for two
tasks × a passing and a failing deliverable. See
`fixtures/production/README.md`.

WSB-02 owns the judge and WSB-07 owns execution. A rubric-scored task scores
`rubric_deferred` here rather than receiving a fallback number, so a judged
task can never look scored when nothing judged it.

## Standalone metering

Searchlight (SWX) runs this workbench without Agent OS and needs its own
metering path; that integration is outside this module change. Agent OS runs
resolve `cwp_dispatch.mcp_metering` at wrap time and explicitly pass its import
root in the MCP server environment, including for exported workbench trees.
If the core is unavailable, or the server uses URL transport,
the generated provider-calls/availability.json record captures unobserved coverage and its reason.
GAP and bake-off reports show observed cells per arm. Completed matching
provider transcript calls prove availability, including with a stale discovery
snapshot. Otherwise an observed session requires successful initialization and
nonempty tool discovery. An installed wrapper without child observation is
unavailable only after harness success or a first-output marker; pre-ready
failures and uninstalled/core-unavailable/URL sessions without completed calls
stay unknown and graded. Use `provider_available` for the final verdict; the
stored `availability` field describes discovery only. Missing metering never
proves zero vendor spend.

## WSB cost model

`lib/python/sew/cost_model.py` turns two unrelated meters into one USD cost
record per cell: vendor API spend (per call) and harness model spend (per
token). Rates live in `config/price-table.yaml`, and the loader refuses any
rate without a source and read date. For Claude Opus 5.5, the table records
base input ($4/MTok), 5-minute and 1-hour cache writes ($5/$8/MTok),
cache reads ($0.20/MTok), and output ($20/MTok). The current Claude harness
retains cache creation as a subset of the reported `input` bucket. When the
Claude result includes a per-TTL split, cache writes use their respective
rates. Without the split, writes use the cheaper 5-minute rate as a lower-bound
`estimated` component. Older bundles without a write count keep the base-input
lower bound and are also `estimated`; neither case is labeled `inferred`.
Claude models without a recorded cache-write rate retain a base-input lower
bound and are marked `estimated`. Cache reads are separate and use their
published rate. Only `claude-opus-5-5[1m]` is
allowlisted to use the base row, because Anthropic publishes standard pricing
for that context variant.
Each affected run lists the fallback reason in `cost_pricing_notes` in
`bakeoff-report.json`.

Claude Code native WebSearch is $0.01 per search; WebFetch has no separate
tool fee, and a search whose transcript explicitly marks the tool result as
failed has no search fee. Tavily extract with text-only output uses the
requested URL count as an upper bound for successful URLs. Its tariff-derived cost is labeled
`estimated_from_request` in the call record and `estimated` in reports;
request records retain the count and never the URL values.

Every component states how its dollars were obtained:

| basis | meaning |
|---|---|
| `measured` | the provider's response stated the dollars (Exa `costDollars`); stamped with the call's `ended_at` |
| `inferred` | a quantity times a price-table rate: Firecrawl/Tavily credits at an assumed plan rate, a per-request list price (Brave, Perplexity, Parallel) for the `pricing_tier` the caller or the MCP meter's tariff declared, or measured tokens at the model's published rates. The component carries the rate, its source, and its date |
| `estimated` | a tariff price applied to an upper-bound request count (Tavily extract URLs), or a lower-bound cache-write calculation when Claude's TTL split or either harness's write count is absent; shown as estimated in reports |
| `unknown` | neither was possible (a tool with no tariff, such as Parallel's MCP search or Brave's summarizer; a Codex model with no recorded rate; a Codex aggregate exceeding the short-context threshold, which only per-request usage could resolve because the long-context rate reprices a whole request; a failed call that reported no spend; unknown tokens). Amount is `null` with a reason |

A cell with any unknown component has an unknown total. `summarize_arm_costs`
leaves those cells out of the numerator *and* the denominator and counts them;
an arm with no costed cell is omitted from `render_cost_table` and listed in its
warnings instead of appearing as `$0.00`. The headline is `usd_per_success`
(spend over successful tasks), which is `null` with status
`undefined_zero_successes`, not infinity, when an arm never succeeded.

Model tokens are the WSB-03 `account_tokens` record: `input` is already net of
`cached_input`, and reasoning tokens are priced inside `output`, never twice.
Refresh a rate by editing the table with the new `source` and `as_of`; do not
add a rate nobody has read from a pricing page.

The first live WSB trial (`wsb-live-egress-live-70e03a13933e`, 2026-09-27)
also defines three cost boundaries. Its Codex cells have no recorded model ID:
the ephemeral `codex exec --json` transcript starts a thread without naming
the model, so historical model spend stays unknown. New cells pin a model only
when the source Codex profile or the cell explicitly declares one. A source
profile using a custom `model_provider` cannot be carried into the isolated
child, so its model stays unpinned and unknown; the same rule applies to Codex
judges, whose resolved model is recorded in the judge record. Codex
`tavily_crawl` calls are recognized by name but stay unpriced without a credit
receipt because crawl cost varies with pages traversed. Codex Brave calls
marked failed returned `No web results found`. The pinned Brave MCP server
@brave/brave-search-mcp-server@2.1.4 throws on non-2xx
(BraveAPI/index.js:105-115) and emits that exact web result only after the
request succeeds (tools/web/index.js:25-38). Brave [bills successful API responses only](https://api-dashboard.search.brave.com/documentation/guides/rate-limiting)
(read 2026-09-27). New web call records with the exact result carry
`error_kind: empty_result` and `meter.basis: billed_empty_result`, so reports
price their `search:request` tier. Historical records have neither marker and
stay unpriced; response length alone is not a receipt. Thrown HTTP errors keep
`error_kind: http_error` and `unpriced_reason: http_request_failed`. The summarizer's
tools/summarizer/index.js:27-54 emits the same error text after a caught
request failure, so its errors remain unpriced.
OpenAI [lists a Web search API tool rate](https://developers.openai.com/api/docs/pricing)
(read 2026-09-27), but the Codex CLI native `web_search` meter is not identified
as that billed API tool. By operator decision (2026-09-30), Codex native search
is priced at the Brave Search per-request rate as a proxy for its backing
vendor: the price table's `codex-native` row has `rate_basis:
operator_proxy_rate` and names `proxy_for: brave`, and a proxy row without
`proxy_for` is refused. Each completed `web_search` item with a `search`
action, or an action Codex does not expose (`other`), is one `search:request`;
`open_page` and `find_in_page` add no tool fee, as with Claude's WebFetch.
If completed Codex `web_search` items outnumber the live provider-call meter,
the run is left unpriced with `search_meter_mismatch`; a larger meter is also
left unpriced as missing search-call evidence.
Claude Code native search keeps Anthropic's published rate.

The no-search Claude outlier `sew-39414881d37d5569` is measured generation,
not a token sum over repeated stream events. Its result reports 128,377 output
tokens, 118,751 cache-write input tokens, two turns, and $3.161319 of model
cost; a synthetic continuation says `Output token limit hit` after the long
first turn. The final result usage is what `_claude_usage_row` records.

## WSB-09 comparative bakeoff report

```bash
modules/search-evaluation-workbench/bin/hq-sew bakeoff report <suite-run-root>
```

writes `bakeoff-report.json` and `bakeoff-report.md` to `<suite-run-root>/reports/`
(`--output-dir` moves them, `--price-table` swaps the rate table). The headline is
task completion rate with a 95% Wilson interval, measured tokens per task, and
dollars per successful task, per arm and per task class, plus token and success
deltas against the no-search control and against native search.

| Rule | How |
|---|---|
| A rate counts only runs the arm decided | *Attempted* runs are `succeeded`, `failed`, `timeout`, and `budget_exhausted`. `contaminated` (also re-read from the WSB-06 arm audit), `not_applicable`, `provider_unavailable`, `harness_boot_failed`, and `cancelled` leave the denominator and become marks. A delivered run with no pass/fail verdict is `ungraded`; its cell's completion rate and $/success are withheld, because a rate over the graded failures alone would be manufactured. The outcome rate over graded runs is still published beside it with the ungraded count (`withheld (ungraded); graded 50.0% (1/2, 1 ungraded)`), so a cell never loses its outcome rate. |
| Tokens and dollars are measured or absent | Tokens per task is the mean *measured* usage over attempted runs, printed as `tokens (measured/attempted)`. Every cell also reports tokens per success (measured tokens over graded runs / measured passes), with the measured-pass and graded-run counts beside it. The token mix is fresh input (cache writes included) / cache reads / output (reasoning included); it includes only measured runs whose nonnegative integer buckets sum to `total_billable`. The mix buckets sum to the mean total over those `mix_n` runs; partial bucket coverage can make that mean differ from tokens per task. Unknown or estimated usage is excluded from token and cost tables with a warning. Cost is the WSB-04 `cell_cost` of each run; live usage buckets are reshaped to its convention (reasoning back inside output) only when they provably sum to `total_billable`. A run whose search calls have no priced provider-call record has an unknown cost, never a model-only one. |
| Deltas name their control and denominator | Controls are declared (`DEFAULT_CONTROLS`: `no_search` = `<harness>+no-search`, `native` = `<harness>+native`) and matched on harness, model profile, and task class. Success change is in points with a Newcombe interval; token change is `arm tokens/task / control tokens/task` with both sides' measured counts, and `tokens/success ratio` compares what each side spent per passed task. A delta against a missing, empty, or under-sampled cell is marked and left blank. The pooled row per arm uses only the task classes where both sides are publishable. |
| Nothing is dropped or crowned | Every arm x task-class cell appears in the detail table with its marks; `marked_cells` links each mark to the runs it affects; every aggregate lists its run ids and links their evidence bundles; the evidence index links each run's bundle, transcript, usage, evaluation, and provider calls. The per-arm headline is a labelled roll-up of the arm's publishable classes, not a ranking. |

Three gaps the report currently makes visible rather than hides:

- The suite runner marks every `no-search` cell `not_applicable`
  (`provider_lacks_search`) in both modes, so deltas against `no_search` read
  `control_no_attempted_runs` until the runner schedules that arm.
- Live cells keep the driver's `pending` evaluation until grading is wired into
  the runner, so live completion rates are withheld as `ungraded`.
- Live provider arms are metered per call by `sew.mcp_meter` (see "Cost
  metering"). A run's cost is still unknown (`N_search_calls_unpriced`) when its
  provider server is remote (a `url` entry cannot be wrapped), when a call was
  not answered or has no record, or when a recorded call is unpriced (no
  tariff for the tool, an argument that changes the price, or an unrecognized
  argument value).
- Codex native search uses the operator's Brave per-request proxy for each
  completed `web_search` item, even if that item's action batches queries.
  Published cost notes identify the stand-in vendor.

## WSB-10 reproducibility bundle and published report

```bash
modules/search-evaluation-workbench/bin/hq-sew bakeoff bundle <suite-run-root>
modules/search-evaluation-workbench/bin/hq-sew bakeoff verify <bundle-dir>
modules/search-evaluation-workbench/bin/hq-sew bakeoff explain <run> --task <task-id>
```

`bundle` writes `<suite-run-root>/publication/<suite-run-id>/` and a reproducible
`<suite-run-id>.tar.gz` beside it (`--output-dir` moves both, `--no-archive` skips
the archive, `--price-table` swaps the rate table). An existing bundle is never
overwritten. If archive creation fails after the directory is published, a
repeat `bundle` command verifies that directory and resumes archive creation;
it never rewrites the published files. Indexed run and attempt directories must
resolve beneath the suite's `bundles/` directory before any run evidence is read.
`verify` and `explain` apply that boundary to published run and attempt paths too,
including symlink traversal.
The bundle holds:

| Path | What it is |
|---|---|
| `README.md` | The shareable headline: completion, tokens, and $/success per arm, and the pooled deltas, each beside the run id, its n, its 95% interval, and its telemetry gaps. |
| `runs/<id>/` | A self-contained suite run root: runner state, the run index with run dirs relative to it, and every run's records (task prompt, deliverable, transcript, usage, provider calls, evaluation, spawn metadata with the WSB-06 arm audit), including retried attempts. |
| `runs/<id>/reports/` | The WSB-09 report, generated from the bundle itself. |
| `manifests/` | The task manifests and catalogs the report read task classes from, `arms.json` (each arm's declared tool surface), and the price table snapshot. |
| `redaction-report.json` | Every stripped field, by file and JSON pointer, with the rule that stripped it and the captured file's sha256. |
| `bundle-manifest.json` | The file inventory (sha256 and size), report schema version, and aggregates digest. |

| Rule | How |
|---|---|
| The bundle re-derives the report, or it is not written | Before publishing, the bundler compares the aggregates of a report over the bundle with a report over the source run: every rate, interval, token figure, cost, delta, mark, warning, and run disposition. Only evidence links and `generated_at` are left out, because they are paths and a clock reading. Any difference refuses the bundle. `verify` repeats that derivation wherever the bundle is unpacked, checks every file against the inventory, and re-renders both Markdown reports to catch a rewritten headline even when inventory hashes were updated. |
| Older report versions are identified separately | `verify` checks inventory hashes and credentials for an older immutable bundle, then returns `report_version_status: older_report_version` with `ok: false`. It leaves aggregate and Markdown drift empty because the current report renderer cannot re-derive that older version. Manifest/report version disagreement returns unsupported status and explicitly says aggregates were not re-derived. Legacy manifests without a version return `legacy_report_version_unverified`: they remain recoverable but their age is not independently authenticated. Hash-clean, credential-clean older bundles can be archived on a repeated assemble. A report from a future or unknown version returns `unsupported_report_version`. |
| Redaction is the default | Transcript text outside protocol fields (the prompt, model output, and tool inputs and results) and harness stderr are replaced with `<redacted:raw-transcript>`. Protocol fields (event types, tool names, call ids, models, timestamps, usage counters, the announced tool list) are kept only in recognized event, message, and typed-block positions. Codex `web_search` items additionally keep `item.type`, `item.id`, and `item.action.type` for native price re-derivation; action queries, URLs, and other nested payloads are withheld. `--include-raw-transcripts` is the operator's per-run opt-in. The task prompt is still published, in the task manifest and `<run>/artifacts/prompt.md`, and so is each deliverable, because a grade cannot be disputed without it. |
| Every stripped field is listed | Along with the bundler's own strips, the redaction report lists every `<redacted...>` and `<elided:...>` placeholder the capture already left, and each absolute run-dir path it rewrote. |
| No credential material, even under opt-in | Every record goes through the credential scrub: values under credential-named keys, credential-shaped text (auth and cookie headers, bearer and API tokens, using the fleet's HRR-09 vocabulary), and every nonempty literal value of a credential env var on the bundling host, including short values. Explicitly known broker-mode flags and credential-file locations are excluded. The finished bundle is then swept with three independent checks: credential shapes, known credential values, and a second scrub pass that must find nothing left to strip. The bundle is assembled in a staging directory and moved into place only after the sweep, so a refused bundle never exists at its destination. |

`explain` takes a suite run root, a bundle directory, or a suite run id under the
SEW state root. It prints one row per arm for the task: passed/attempted, the
evaluation dimensions its failed runs scored false (`completed` for a run that
ended before delivering), measured tokens per task, and notes for runs outside
the denominator (contaminated, ungraded, timeout, ...). Below that is a
per-run table linking each run's bundle, transcript, usage, and evaluation.
Dispositions and token provenance come from the WSB-09 report, so the two views
cannot disagree.

Each run's own `<run>/evidence/bundle.yaml` still describes the capture: its
`size_bytes` are the captured sizes. `bundle-manifest.json` describes the files
that were actually published.

## Grading live bakeoffs

After a live suite finishes, run `SEW_HARNESS_LIVE=1 hq-sew bakeoff grade <suite-run-dir>` before `bakeoff bundle`. Use `--dry-run` to see each cell's grader without spawning a judge. Deterministic tasks score locally; rubric tasks use a blinded Claude Code judge by default. `--second-judge-harness codex` records agreement without changing the primary verdict. Grading tokens are recorded in each run's judge record (evaluations/judge.json) and are excluded from arm cost. Use `--regrade` to replace an existing grade before publishing the bundle. Each judge call is capped by `--judge-max-tokens` (default 300,000, which counts the harness boot and cache reads) and `--judge-timeout-seconds`; a call that hits either records its `transport_error` in the judge record and leaves the cell ungraded. A rubric verdict never overrides the deterministic checks: a schema-invalid or unsafe deliverable fails whatever the judge scores.

A non-JSON judge reply gets one retry with the remaining token and time budgets
only when the first attempt reports token usage. If usage is unavailable, the
cell remains ungraded rather than risking a second full-budget call.

## Harness auth

Live cells and rubric judges use a fresh OAuth broker credential for each spawn when
`OAUTH_BROKER_SHARED_SECRET_FILE` (or the deploy checkout's broker secret) is
readable. Claude Code receives `ANTHROPIC_AUTH_TOKEN`; Codex gets an isolated
Codex auth file with the worker sync helper's placeholder refresh token.
Codex receipts must have a timezone-aware `last_refresh` and a finite expiry
more than 120 seconds away, leaving time for harness boot and its first call.
The refresh stamp permits up to 60 seconds of future clock skew and, when `tokens.fetched_at` is
present, must match it within one second (allowing timestamp rounding).
Expired or nearly expired receipts fail closed and remove any stale cell auth.
`hq-sew doctor --codex-broker-auth` validates this path without a model turn.
On failure it prints the scrubbed diagnostic before deleting the temporary
check directory; the printed file path is temporary, and its contents remain
in the command output for activation troubleshooting.
A broker fetch retries transport timeouts and transient network failures up to
three times with bounded backoff. A permanent broker refusal fails immediately.
Receipt validation happens after the fetch: expired, near-expiry, or malformed
receipts are refused immediately without a re-fetch. The broker's 25-minute
refresh window normally keeps served tokens outside the 120-second refusal
margin; stale cached receipts during an upstream outage still fail closed.
If the fetch still fails, its stderr is saved under the cell bundle's `logs/` as a
mode-0600 `claude-code-broker-auth-stderr.log` or
`codex-broker-auth-stderr.log`; spawn metadata records its relative path, and
the bundle lists the scrubbed diagnostic and broker receipt. Both new and
existing diagnostic files are restricted to mode 0600 before writing. The cell
records an auth boot failure without starting the harness. Judge auth failures
include a short, scrubbed diagnostic in the judge's `transport_error` record.
Use `--harness-auth broker|account` on `run`, `run-live-harness`, or `bakeoff
grade` to select explicitly. `account` preserves the local login behavior except
for Linux Claude GAP code cells, which require broker OAuth and refuse account
auth before spawning. Their scratch HOME/config contains no host login or account
metadata, so sandbox token refresh cannot invalidate a host account. Standalone
Linux code evaluation can use Codex; Claude brief cells still support account auth.
Spawn metadata names the selected source and broker expiry/fingerprint, never
the token.

## GAP job catalog schema

`sew.gap.catalog.load_gap_tasks(root=None)` returns validated job mappings keyed
by id; `validate_gap_catalog` returns ids. `root` is a SEW module root. Both
read only `catalogs/gap/tasks.yaml`, inspect asset paths, and never open hidden
assets or fetch wheels. The shipped catalog is empty until GAP-07/GAP-09;
`tests/fixtures/gap` contains synthetic schema goldens, not admitted jobs.

Schema version 1 requires `id`, `family`, `kind`, `task_type` (`code` or `brief`),
`prompt`, `hidden`, `packages`, `oracle`, `verifier`, `provenance`, `cutoff_after`,
`budgets`, and `giveaway_terms`. Families are `api-break`, `silent-default`,
`release-breakage`, `vulnerable-dependency`, `changed-third-party-api`,
`decision-brief`, `research-brief`, and `control`. Only the control family uses
`kind: control`; controls may be code or briefs. Unknown fields are refused.

- Code tasks add `fixture` and `third_party_packages` (explicit dependency names;
  empty for standard-library controls). Fixture and hidden directories must
  exist, resolve within the catalog, and be disjoint. Fixture symlinks must stay
  within the fixture so workspace copies cannot expose verifier data.
- Packages are a list of `{name, version, url, sha256, role}` pins. URLs name HTTPS
  wheels; roles are `old`, `new`, or `dependency`. Each declared third-party
  package must be pinned. Gap code tasks require distinct old/new versions of
  at least one same package. Controls can use one dependency pin per package.
- `oracle` contains inline `excerpt`, `source_url`, `retrieved_at` (ISO date), and
  lowercase `sha256` of the exact UTF-8 excerpt bytes, including whitespace.
  The excerpt is nonempty and at most 400 whitespace-delimited words. Authors
  must verify primary-source provenance and verbatim fidelity during review;
  the offline loader checks metadata and integrity, not remote authenticity.
- `giveaway_terms` declares changed API identifiers and versions from the oracle.
  Each must occur in the excerpt and must be absent from the prompt, using
  case-insensitive matching with word boundaries. Gap tasks require a nonempty
  list; authors must enumerate all giveaways, since semantic hint detection is
  outside this gate.
- `verifier` contains nonempty lists `visible_commands`, `hidden_commands`, plus
  positive integer `timeout_seconds`. The loader never executes commands.
- `provenance` contains `event_date` and nonempty HTTPS `source_urls`.
  Gap events must postdate the ISO `cutoff_after` date.
- `budgets` uses SEW's `max_total_tokens`, `max_wall_clock_seconds`, and
  `max_provider_calls` integer keys. Tokens and time are positive; calls may be zero.
- Briefs add a nonempty mapping `deliverable_schema` and a catalog-relative
  `rubric` file reference. The loader checks its existence without reading it;
  GAP-08 owns deliverable semantics and rubric grading.

All asset paths are relative to `catalogs/gap/`; traversal and symlink escapes
are refused. No GAP tasks enter the production catalog or its execution lane.

### GAP execution grading

GAP code cells use `sew.gap.verify.verify(task, diff, wheelhouse=...)`, never
final-answer fields. `grade_run(..., wheelhouse=...)` stores the execution record
under `evaluation.execution`, preserving arm usage and cost. The existing
`bakeoff grade` command accepts `--wheelhouse <cache>` for captured GAP cells.
A missing `<run>/artifacts/workspace.diff` stays ungraded with the canonical
`deliverable_missing` reason, rather than an execution failure. An existing empty
diff is a valid no-change deliverable.
Ordinary verification installs the catalog's new/dependency pins, regardless of
old, empty or absent fixture requirements. Requirements may contain only
catalog-pinned wheels; they cannot select the old API for grading. Explicit
`install_roles` overrides are reserved for author-validation legs, including the
old-visible check. The corpus also checks ordinary grading without those
overrides: unchanged gap fixtures fail and reference patches pass.
Security jobs additionally require the reconstructed deliverable's requirements
to select the fixed catalog pin; code-only repairs retaining vulnerable pins fail
even though grading runs against the fixed dependency.
Run admitted tasks with `hq-sew gap run --harness codex --model <model>
--arm floor --arm ceiling --arm native`. `--dry-run` projects cells, tokens and
model cost from calibration; `--run-root <outside-tree-path> --resume` continues
an interrupted battery. The [GAP runbook](RUNBOOK-gap.md) covers service-account
environment, canaries, budgets, provider outages and report/refresh commands.

`hq-sew gap calibrate --harness codex --model <model>` runs floor and ceiling
references (five repetitions by default). Each cell retries the suite runner's
`failed`, `timeout`, and `harness_boot_failed` statuses up to three total attempts,
waiting one then two seconds through the shared backoff helper. Retry bundles
have `-att2` / `-att3` suffixes; only a successful, clean, model-matched attempt is
graded and counted. Every attempt must pass the identity and contamination checks
before a retry. Other statuses, exhausted retries, or verifier infrastructure errors
refuse admission; they never become task failures in the admission denominator.
Records under `<state-root>/gap/calibration/<harness>@<model>.json` retain each
cell's `attempts` and ordered `attempt_run_dirs`, including failed attempts.
Bundles remain under `<state-root>/gap/calibration-runs/<uuid>/` on failure;
calibration writes its admission record only when all requested cells complete.

Brief calibration captures cited sources through SEW's public-address resolver
and DNS-pinned HTTPS transport, with proxies and redirects disabled. URLs must
have no inline credentials, and every resolved address must be globally routable
and unicast. The original hostname is used for TLS verification. Capture sets a
SEW user-agent header, honors the response charset with UTF-8 fallback, replaces
invalid bytes, and retains the 30-second socket timeout and two-million-byte
response limit. Refused or uncapturable sources count as unsupported claims.

Verification reconstructs the fixture plus the bundle’s workspace diff in a
new temporary directory, checks and copies the catalog-pinned wheels, and
installs in a fresh virtual environment with no index or host task dependencies.
A fixture's requirements.txt may select exact `name==version` catalog pins;
without it, verification installs the new and dependency pins. URLs, requirement
includes and unpinned packages are refused. pytest and its supporting libraries
are copied from the installed pytest distribution and its active declared dependency
closure, including distribution metadata. Optional extras are excluded and ambient
pytest plugins are disabled; no fixed list of internal pytest imports is required.

Patch application, installs and every visible, hidden and rerun command execute
under a deny-by-default macOS Seatbelt profile (`/usr/bin/sandbox-exec`) or
a qualified Linux bubblewrap network namespace. Patch
application reads the diff through stdin and can write only to the reconstructed
workspace; its resolved Git runtime is allowed read-only. Installs can write to
scratch, including the venv. Tests can write only to the workspace and their own
per-command report/temp directory (also used as HOME and TMPDIR). The sibling venv
and wheelhouse remain read-only, so visible tests cannot replace the runner used
by hidden tests or reruns. System/Python runtime files and Homebrew keg libraries
are read-only; process execution/forking and selected runtime sysctls are allowed.
Network access and operator files are denied; child processes inherit the same
policy. Linux binds the system/Python runtime read-only into a fresh root, mounts
`/tmp` before explicit read and scratch binds, and refuses host-root read binds.
The verifier refuses execution when the selected backend is unavailable or Linux
qualification fails; a virtual environment alone is never accepted as containment.
`verify(..., environ=...)` selects the backend from the supplied environment once.
Grading, validation and calibration currently use the process environment; set
`SEW_CONFIG` there to select their backend. Harness admission uses the run source
environment. The verifier selects its backend
and uses it for patching, installs, tests and reruns.
The command process group is killed on every exit, including success and timeout,
before the leader is reaped, reserving its PID against reuse during cleanup.
Exit observation uses waitid with WNOWAIT, or kqueue NOTE_EXIT on macOS Python
3.11/3.12 where Python does not expose waitid.
Local stubs that require sockets are also denied by this offline profile.

Hidden assets enter only the verifier copy after visible commands finish; any
visible-test collision with the hidden destination fails verification. They are
materialized at their catalog-relative path,
after installation. Commands must use `python -m pytest` or
`python3 -m pytest`; the verifier adds a separate JUnit report for each command.
A pass requires every visible and hidden test to pass with a successful command
exit. Skips, empty or unreadable reports, install errors and timeouts fail.
Hidden failures are rerun once using pytest's failed-node cache; execution errors
without test results rerun the command. Initial failures still count as escaped
defects and fail the cell, even when their rerun passes and records a flake.
Temporary verifier inputs are removed on completion; only results and bounded
command diagnostics are retained in the evaluation.

The provider meter adds SEW and worker-pool import roots only to its own
bootstrap environment. Both the normal relay and the unavailable-core fallback
remove these roots before starting the vendor server, while retaining other
operator-provided Python paths and provider configuration variables.

### Host modes and diagnostics

`bin/hq-sew doctor` reports the selected host, why it was selected, the state
root, harness authentication route, provider key presence and sandbox availability.
By default it never fetches credentials or tests a live broker, and labels login as
unverified. It runs a parent-owned backend canary with a 12-second overall cap:
curl and isolated pip must show network-denial errors, and TCP/DNS socket probes
must report denial errnos. pip uses `-vv` to expose connection errors; generic
package-not-found output does not qualify. Setup errors report an inadmissible
backend instead of crashing doctor. Agent OS `op://` provider references count as
configured keys without fetching or verifying their values.

For Agent OS activation, `OP_BIOMETRIC_UNLOCK_ENABLED=false SEW_MODE=agent-os
sew doctor --codex-broker-auth` runs only the broker probe, skipping this host and
sandbox report. It validates a receipt in a disposable private Codex home without
a model turn. Success prints `codex broker auth validated; expires_at=...`;
unavailable or invalid auth exits nonzero. Failures print scrubbed exception text
and any scrubbed diagnostic before temporary files are removed; if scrubbing
fails, only the exception type is printed. Standalone refuses the broker probe
and directs users to account login.

Codex requires a finite expiry more than 120 seconds away and a timezone-aware
`last_refresh`, allowing at most 60 seconds of future skew. An optional
`tokens.fetched_at` must be finite and match the stamp within one second.
Malformed fields surface fixed, non-echoing reasons to live cells and doctor.
Invalid receipts remove stale cell auth and fail without re-fetching; transport
failures retain bounded retries. There is no maximum refresh-stamp age: the delegated SRE operator decision
for PR 6 (2026-10-05) preserves this policy in both validators. No confirmed
Codex refresh interval exists; codex-auth-refresh and live broker/fleet-quota
probes own token freshness, while doctor validates canonical auth structure.
An arbitrary age bound risks false-fail outages.

Set `SEW_MODE=standalone` to use account CLI logins and local MCP metering without
loading any Agent OS packages. State defaults to `$XDG_STATE_HOME/sew`, or
`~/.local/share/sew` when XDG is unset; `SEW_STATE_ROOT` overrides either host.
Provider keys use `SEW_*_API_KEY`, `env:` references in `SEW_*_API_KEY_REF`, or
literal assignments in a local `.env` (`SEW_ENV_FILE` selects another file).
Environment values override `.env`. Standalone mode refuses `op://` references
and explicit broker authentication.

The default mode, `auto`, discovers the `agent-os` entry in `sew.hosts` from an
installed `agent-os-app-sdk`. If its detection succeeds, state, secrets-bus
credentials, OAuth broker authentication and host metering use that plugin;
otherwise the workbench selects standalone and explains why. Explicit
`SEW_MODE=agent-os` fails with the detection reason if the plugin is unusable.
The SDK requires the loaded service-account environment for credential resolution.

A `mode:` setting in `sew.yaml` supplies the same choices. Discovery checks the
current directory, then `$XDG_CONFIG_HOME/sew/sew.yaml` (default
`~/.config/sew/sew.yaml`); `SEW_CONFIG` selects a specific file. `SEW_MODE`
overrides the configuration file. Unknown configuration keys are ignored and a
missing file uses defaults. If PyYAML is unavailable, file configuration is
skipped and environment settings or defaults apply.


### In-tree host discovery

The portable `bin/hq-sew` launcher respects an explicit
`SEW_AGENT_OS_SDK_PATH` supplied by its caller. Agent OS normally installs the
optional SDK extra; source integrations may instead supply its
`platform/app-sdk/python/src` path. When no installed `sew.hosts` entry point loads
and detects an available host, SEW tries the SDK from that explicit path and
applies the same read-only host detection. No package installation or checkout
build is needed. Auto mode uses
agent-os when HQ and host services are available; `SEW_MODE=agent-os` fails
closed if they are unavailable. `SEW_MODE=standalone` skips both entry-point
and source-path discovery, including all Agent OS imports.

The SDK package load uses explicit package search locations; its config bootstrap
may still add `platform/agent-os-config/src` to the interpreter's global `sys.path`
and process that directory's `.pth` files. An already-imported SDK must match the
supplied source path or this candidate fails. Discovery tries installed candidates
independently, then the source candidate, and reports the last attempt's reason
if none is available. A broken installed entry point or an unavailable installed
host does not suppress the explicit source candidate. Both the loaded SDK package
and its host module must resolve to that source. Exception details are withheld
from diagnostics.

`SEW_AGENT_OS_SDK_PATH` is inherited by child processes, so nested Searchlight
invocations also opt into source discovery unless that variable is unset.
Searchlight without this environment opt-in retains installed-entry-point discovery.
Broker or `op://` requests after an auto fallback report standalone mode,
the detection reason, and the launcher/SDK installation fix. Default `doctor` inspects
configuration without fetching provider credentials or contacting the broker.

Linux qualification uses `/usr/bin/bwrap` and requires a canary reporting only
`lo` in its network namespace, in addition to explicit TCP, DNS, curl and pip
denials. IPC, UTS and available cgroup namespaces are isolated too. Transient
launch errors retry at most three times with bounded backoff; failed containment
evidence is never retried. Qualification refusal leaves execution grading pending
(`not_applicable`, `sandbox_unavailable`) rather than scoring the patch as failed.
The proxy relay preserves half-closed responses and keeps transport errors out of
harness stderr. Refusal reasons use the canary credential and transcript scrubbers.
Real Seatbelt tests skip a nested `sandbox_apply` refusal unless
`SEW_REQUIRE_SEATBELT=1`; doctor unit tests stub local qualification.


### GAPWHEEL-01 wheel visibility and attempt evidence

Agent code cells receive only catalog package roles `old` and `dependency` in
their offline wheelhouse. `prepare_workspace(..., wheel_roles=...)` defaults to
those roles; the out-of-band verifier explicitly prepares `old`, `new` and
`dependency` outside the agent's readable paths. The existing read-only sandbox,
network denial and verifier qualification requirements remain in force.
Code-cell setup removes caller-supplied pip settings and applies only the
driver's explicit offline allowlist; extra effective `PIP_*` settings refuse
the configuration-neutralized exemption, which otherwise covers only a strict
allowlist of direct, pinned pip commands (no shell wrappers, pipes or chains). On macOS the copied wheelhouse is
immutable across the harness tree, including non-shell writes and ancestor
renames. Admission qualifies creation, modification, chmod, hard-link and
directory-rename denials while retaining readable local wheels; evidence is
recorded in `direct_egress.wheel_cache_isolation.cell_wheelhouse`. Linux retains
its read-only copied-wheelhouse mount. See
[GAPWHEEL-01](lib/python/sew/gap/WORKSPACE.md#gapwheel-01-verifier-cache-containment)
for the complete admission contract.
`ArmAuditor.new_package_attempts` records heuristic observations of shell commands
naming a new package/version; it is audit evidence, not proof that every possible
acquisition command was detected. Spawn metadata persists that evidence under
`arm_audit.new_package_attempts`. Qualification refusal remains a verifier setup
refusal (`not_applicable`, `sandbox_unavailable`) rather than a patch failure.
