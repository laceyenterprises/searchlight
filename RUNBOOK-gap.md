# Running the gap bench

Run these commands from the SEW module directory, using its `bin/hq-sew`.
Calibration and batteries consume model quota; reserve a suitable budget before
live execution. The automated suite uses fixtures and offline verifier jobs.

## Environment and egress canary

In standalone mode, supply provider keys through `SEW_*_API_KEY` environment
variables or an untracked local `.env`, and use each harness's account CLI login.
No service account or secret-manager access is required. Set `SEW_MODE=standalone`
to bypass optional host discovery.

In Agent OS mode, secret-manager provider references require an **already
provisioned service-account environment**: `OP_SERVICE_ACCOUNT_TOKEN` exported
and `OP_BIOMETRIC_UNLOCK_ENABLED=false`. Load that environment through the host's
trusted launcher. Never fetch the service-account token interactively, print it,
paste keys into configuration, or resolve secret-manager references without that
environment. An absent token must abort secret-manager resolution.

Live harness execution additionally requires `SEW_HARNESS_LIVE=1`. Use the same
explicit harness and model for calibration and the battery. Provider MCP config
is a YAML mapping of provider IDs to server configs, with `${SEW_*_API_KEY}`
environment references, as described in the [README](README.md). Supply an
operator-owned config outside the repository as `provider-mcp.yaml` below;
never commit resolved configuration or raw keys. No provider config is needed
for floor, ceiling or native alone.

For hosted endpoints through `mcp-remote`, keep credentials out of process
arguments. References in `args` must be named `URL`, `ENDPOINT`, `HOST`, `PATH`
or `DIR`, or end with one of those suffixes after `_`, and contain no secret or
auth marker (case-insensitive). Resolved args are also refused when they contain
bearer/basic/token credentials, Authorization headers or secret assignments.
For example, `Authorization:${AUTH_HEADER}` is refused even though the upstream
README uses it. Ordinary URL/path references still expand. Use `env` for servers
that accept credentials through their environment, or this header-file pattern:

```yaml
parallel-web:
  command: npx
  args: ["-y", "mcp-remote@0.14.3", "https://search.parallel.ai/mcp-oauth"]
  header_from_env:
    name: Authorization
    value: "Bearer ${SEW_PARALLEL_WEB_API_KEY}"
```

SEW appends `--header-file PATH` pointing to a 0600 file in a 0700 `.secrets`
subdirectory of the cell's temporary scratch directory. Teardown removes it on
success and error. These permissions exclude other OS users, but do not isolate
the file from an agent running as the same user; treat provider results as
untrusted and avoid giving the agent instructions to read credentials.
The resolved header stays out of arguments and evidence bundles.
The published `mcp-remote@0.14.3` distribution's `parseCommandLine` handles
`--header-file` through `readHeaderFile`; keep that exact pin. Its `--header`
also expands environment references, as described in the
[upstream README](https://github.com/punkpeye/mcp-remote#custom-headers).
SEW resolves references before spawning, so use `header_from_env` for credentials.

Credential cleanup removes non-empty exact header values and, for any
`<scheme> <credential>` header, the credential portion from captured output.
Artifacts are scrubbed as bytes, including non-UTF-8 files, preserving CRLF and
rewriting only files whose content changes. Artifacts that cannot be read or
rewritten are removed before bundling; sweeping then continues. If removal also
fails, finalization fails rather than bundling unsanitized evidence. The final
secret scan also checks for recognizable credential text. Empty values cannot
corrupt output by triggering empty-string replacement.

On macOS, Claude Code qualification runs entirely in the bench through srt; no probe
instruction is sent to Claude. See [CANARYOOM-01](lib/python/sew/gap/WORKSPACE.md#canaryoom-01-claude-qualification-outside-the-model)
for the operator decision, strict version pins and residual risk. The historical
agent-run Claude description below is superseded by that contract. Qualification
preserves every sandbox runner option before the `--` separator when adding debug
probes. `qualify_claude_code` owns the scrubbed `egress-canary.json` write on both
success and refusal; the live harness does not rewrite successful evidence.

There is no standalone canary CLI. Before each code calibration or battery cell,
the workspace driver runs curl, isolated pip download and Python HTTPS probes.
Claude uses nonce-bearing HTTPS hosts and requires each exact host/port denial
in the matching Bash result's `<sandbox_violations>` block. Codex requires denial
text in each probe error and uses raw-IP curl/HTTPS targets to avoid DNS dependency.
SEW separately runs a bench-owned direct socket probe to 1.1.1.1:443 before
launching the agent canary, requiring EPERM or EACCES. The probe runs under a
separate sandbox runner: `codex sandbox` (or its `macos` subcommand when
advertised by CLI help), requesting the cell sandbox mode and inheriting its
isolated `$CODEX_HOME/config.toml`. The cell must explicitly set
`sandbox_workspace_write.network_access=false`; missing, malformed or
network-enabled policy and cell configuration/profile overrides refuse admission.
The probe does not override the cell's network policy.

For Claude, the probe runs under standalone Anthropic `srt --settings`, using
the cell's network policy (empty allowlist, global deny) and a bench-synthesized
filesystem policy allowing writes to the workspace and probe scratch directory.
Provision the pinned runner with
`npm install --prefix <dir> @anthropic-ai/sandbox-runtime@0.0.78` and add
`<dir>/node_modules/.bin` to the bench PATH. `sew doctor` reports availability;
an absent runner refuses the cell. Claude Code uses its own bundled runtime;
this standalone probe does not qualify that implementation or its filesystem
policy. Evidence records both srt and Claude Code versions, with bundled runtime
version unknown. The separate agent canary qualifies the cell's shell behavior.

Code cells with a wheelhouse have an outer filesystem-only Seatbelt wrapper
denying verifier-cache access; nested sandbox startup failures refuse admission.
The wrapper adds no network policy. **Non-shell harness descendants and transports, including MCP servers,
WebFetch and harness HTTP clients, have no bench-owned OS egress confinement.**
The proxy environment is advisory for those transports: a client that ignores
it can connect directly to arbitrary external hosts. A successful canary and
`admissible: true` qualify the recorded probes, not the whole harness tree.
Adopting this reduced guarantee requires explicit operator sign-off or a
replacement boundary; this description of present behavior supplies neither.

Recognized nested sandbox startup failures in either stdout or stderr refuse
with a distinct reason and never count as direct denial. Probe execution and
runner help/version discovery retry process timeouts and EAGAIN, EINTR, EIO or
ETIMEDOUT up to three total attempts per call, each with a 12-second timeout and
delays of 0.5 then 1 second. Exhaustion refuses; permanent spawn errors, nonzero
exits, malformed output and unproven denials refuse immediately. Nonzero probe
exits preserve stderr in the exception chain.

Proxy-aware harness API traffic uses a per-cell parent-owned HTTP/CONNECT proxy
restricted to pre-resolved endpoint host/port pairs; configured base URL ports
are preserved (HTTPS defaults to 443, HTTP to 80). Socket creation, setup and
connection errors try the next saved address, allowing IPv4 fallback when IPv6
is unsupported; exhaustion returns HTTP 502. Child proxy variables replace
inherited proxy settings, with local-only NO_PROXY. The proxy shuts down when
the cell ends or refuses. Evidence retains endpoint addresses/authorities,
runner path/version, requested runner policy (`policy_scope`), and
`harness_tree_confined: false`. Runner permission attribution is
`harness-runner-permission:codex-sandbox` or `harness-runner-permission:srt`.
Policy metadata describes inputs, not independently observed applied policy;
the permission-denied socket outcome is the measured result.
Loopback transports remain trusted exceptions.
Successful egress on any probe, incomplete evidence or ordinary network errors
on the first three probes refuse the job. A sandbox block with no recognized
denials reports `unrecognized sandbox_violations format`; inspect CLI wording.
Inspect each
bundle's `<run-dir>/artifacts/egress-canary.json` for `admissible: true`. Calibration is the
first real invocation that exercises this gate, under the environment above.
Brief cells have no shell surface. Live canary qualification is per harness;
offline tests cannot certify the host's sandbox.


Linux selects bubblewrap from the run's source environment (`SEW_CONFIG` and
`sandbox: auto|bubblewrap`), before child-environment filtering. The namespace
has no host TCP/UDP connectivity; the harness uses a loopback relay to the
parent's endpoint proxy through a private Unix socket.
For OSS cells, a loopback LiteLLM base URL keeps its hostname but uses the
relay's port in matching harness environment variables
and the isolated Codex LiteLLM provider config, if that config exists. Missing
`config.toml` is left absent. URL credentials are preserved for the harness's
authentication, while the forwarded authority excludes credentials. Only the
relay's exact hostname and port translate to the original endpoint, whose host,
port and saved DNS answers remain enforced by the parent proxy. Both `NO_PROXY`
and `no_proxy` retain `localhost,127.0.0.1,::1`, so local development servers
inside the namespace bypass the egress proxy. Direct HTTP requests are translated
to proxy requests; direct TLS first establishes an allowlisted CONNECT tunnel,
preserving the original hostname for certificate verification. IPv6 loopback
endpoints use an IPv6 listener. Each qualification or cell launch
refreshes the provider URL for its own listener. `/tmp` is mounted before
read and scratch binds so the socket and any explicitly admitted runtime below
`/tmp` remain visible. A host-root read bind is refused. Backend qualification
runs curl, isolated pip with `-vv`, direct TCP and a DNS datagram: tool failures
need denial text and sockets need EPERM/EACCES or Linux ENETUNREACH. Generic pip
package-not-found output, timeouts and missing tools refuse admission. Network
probes use two-second timeouts, a three-second tool-process cap and a 12-second
overall cap. `sew doctor` executes this same backend qualification on macOS and
Linux, reporting setup errors as inadmissible; it does not certify the separate
model-run shell canary.

Linux Claude code cells are unsupported with either account or broker auth.
The out-of-model srt qualifier has been validated only on macOS; a host srt
probe cannot attest the cell's bubblewrap namespace. A broker-authenticated
cell that reaches qualification is explicitly refused with this reason before
any model turn; account auth remains refused during boundary preflight.
Use Codex for Linux code cells. This supersedes the previous broker-only Linux
Claude workflow; broker auth alone no longer admits a code cell. Claude brief
cells continue to support account or broker authentication. `sew doctor` tests
the backend only and does not establish Claude code-cell support.
An external `CODEX_HOME`
supplies only `auth.json`, `.credentials.json` and `config.toml` to a writable
scratch copy; the normal arm-prepared Codex home already lives in scratch.
Host sessions and config directories are not mounted. These ephemeral copies
are removed with cell scratch; Codex copies are not written back. Broker-mode
Codex uses the existing placeholder refresh token in its scratch auth file.
Every preflight exception retains `reason` beside `admissible: false` in the
refusal artifact before propagating; no model invocation follows a refusal.

## Validate and calibrate

Author validity precedes calibration. The wheelhouse is an outside-tree cache of
hash-verified pinned wheels. Replace `TASK_ID` with a catalog task ID and
`MODEL_ID` with the exact model ID. State defaults to SEW's configured state root,
with calibration at `gap/calibration/<harness>@<model>.json`.

Agents receive only `old` and `dependency` wheels; `new` wheels are verifier-only.
The original cache is denied to the whole agent process tree: a filesystem-only
Seatbelt profile on macOS and an empty read-only cache mount on Linux, applied
after runtime mounts. Cache paths overlapping cell scratch refuse admission.
Before each code cell, bench-owned probes attempt direct reads and discovery
from the cache's parent without the cache pathname, then reading discovered
wheels. Keep the cache under a small dedicated parent directory: discovery is
bounded to 10,000 entries and 12 seconds per attempt, and excludes cell scratch.
Both denials must qualify, and the harness's required nested sandbox must start;
otherwise the cell refuses without a model task. Successful evidence is under
`direct_egress.wheel_cache_isolation` in `artifacts/egress-canary.json`.
See [GAPWHEEL-01](lib/python/sew/gap/WORKSPACE.md#gapwheel-01-verifier-cache-containment).

The copied cell wheelhouse is readable and immutable for the macOS harness and
all descendants, including non-shell file tools. Before admission, bench-owned
probes must demonstrate permission denials for creating HTML links, modifying
files or modes, making hard links, and renaming the wheelhouse or scratch. The
parent-created writable probe marker must still be readable. Failure refuses
the cell before model execution; successful per-operation evidence is retained
in `direct_egress.wheel_cache_isolation.cell_wheelhouse`. Linux retains the
read-only wheelhouse bind. This prevents unverified HTML links from adding remote
sources to the driver's `PIP_FIND_LINKS` directory.

Verifier scratch, copied wheels, installed packages and hidden tests are kept
under `/tmp/sew-gap-verifier-<uid>` (the system `/tmp` alias is resolved). This
fixed per-account root ignores `TMPDIR` and run/state roots. Every macOS agent
profile denies it, including copies created by another run after the cell starts;
the parent and verifier retain access. Admission also qualifies direct and
discovery reads of a copied wheel and an installed-source marker under this root,
recording `wheel_cache_isolation.verifier_copies`. Scratch is cleaned on exit;
the empty root persists. A substituted symlink or foreign-owned root refuses
verification; overlapping cell scratch refuses admission. Transcript audits also
forbid this root. Same-UID directory permissions are not the containment boundary.

```bash
bin/hq-sew gap validate-task TASK_ID --wheelhouse /tmp/gap-wheelhouse
bin/hq-sew gap calibrate --harness codex --model MODEL_ID --reps 5 --wheelhouse /tmp/gap-wheelhouse
```

Gap admission requires floor ≤ 20% and ceiling ≥ 80%; controls require floor
≥ 80%. A contaminated or incomplete reference cannot establish admission.

## Project cost, run and resume

Dry-run reads calibration and its usage bundles, without spawning a harness,
loading provider configuration, reading keys or writing run state.

```bash
bin/hq-sew gap run --harness codex --model MODEL_ID --arm floor --arm ceiling --arm native --arm perplexity --reps 3 --dry-run
bin/hq-sew gap run --harness codex --model MODEL_ID --arm floor --arm ceiling --arm native --arm perplexity --reps 3 --wheelhouse /tmp/gap-wheelhouse --provider-mcp-config /tmp/provider-mcp.yaml --run-root /tmp/gap-battery
bin/hq-sew gap run --harness codex --model MODEL_ID --arm floor --arm ceiling --arm native --arm perplexity --reps 3 --wheelhouse /tmp/gap-wheelhouse --provider-mcp-config /tmp/provider-mcp.yaml --run-root /tmp/gap-battery --resume
```

Use a durable outside-repository run directory for real batteries (the `/tmp`
paths above are examples). `--task` and `--arm` are repeatable. Omitting tasks
selects all currently admitted tasks. Unknown, rejected, retired, differently
calibrated or catalog-stale tasks are refused before spawning. Resume requires
the same matrix and calibration, skips completed cells, and gives interrupted
attempts fresh bundle names. Keep the same wheelhouse, provider config and
harness authentication on resume. A concurrent runner is refused.

Three consecutive `provider_unavailable` cells stop the battery. The streak cells
remain eligible on resume; retry only after the provider recovers. Isolated outages
stay terminal. As in the existing suite runner, a cell deferred twice becomes
terminal on its next unavailable attempt, so repeated resumes cannot starve the
remaining matrix. Outage streaks survive interrupted invocations. Cancellation
also stops resumably. Every cell receives catalog token, search-call and wall
limits; budget exhaustion is terminal and is never graded as a pass. Job usage
is checked again before grading. Failed or contaminated cells stay visible in
the report. Verifier infrastructure errors abort without completing the cell.

Provider availability is resolved by `provider_available`, separately from
metering coverage. Each live bundle records `wrapper_engaged`, `observed`,
`observation_reason` and a discovery-only `availability` snapshot in
`<run-dir>/provider-calls/availability.json`. The stored snapshot is not the
final verdict and stays `unknown` until the child is observed. Live creation
persists the verdict separately as `run.json.provider_availability` before
artifact overflow can change status or drop completed calls; GAP, adoption and
reports prefer that field. For legacy bundles, the shared `sew.harness` gate
accepts succeeded/failed/timeout/harness_boot_failed and leaves auth/spawn failures
unknown even with ready markers. Completed matching Codex MCP items or paired non-error Claude
tool results prove availability even if the child failed to update the file.
Otherwise observed discovery requires successful MCP `initialize` and harness
"tools/list" responses with at least one tool; failed observed discovery becomes
`provider_unavailable` before grading. An installed wrapper that never launches
(`server_not_launched`) is also unavailable once the harness succeeded or emitted
a first-output marker. Parent installation alone does not prove CLI startup:
without those signals, boot failures and pre-ready failures/timeouts retain their
harness status and unknown availability. Other unobserved sessions without
completed calls also stay unknown and graded. Generic live suites, GAP and
crash-recovery adoption use the same resolver. Inspect `unknown_n` when diagnosing
infrastructure failures. Snapshot-write failures emit the
`mcp_meter_snapshot_write_failed` stderr sentinel and attempt to write the sticky
`<run-dir>/artifacts/meter-observation-failed.txt` marker outside the bundle's `provider-calls/`.
The marker is listed in bundle evidence when present. A marker or captured
sentinel keeps availability unknown without completed calls, even after startup;
the transparent relay continues. A discovered provider that the agent never calls is
still graded. GAP reports show availability and metering coverage separately;
unavailable cells are excluded from pass rates, and transcript availability
does not recover missing vendor spend.
Codex provider configs wait for their server (`required = true`) with a default
`startup_timeout_sec = 120`; a positive finite value in the provider config may
override it. The cell's wall-clock budget still applies.

Add `--prewarm-providers` to a battery command to resolve exact pinned packages
from configs shaped like `npx -y package@1.2.3` before cells launch. This uses
`npm exec` to populate the npx cache without executing provider entrypoints or
install scripts, and without passing provider-key environment variables. It
requires npm and registry access; unpinned packages or resolution failures
abort the pre-warm. URL transports cannot currently be observed by the stdio
meter; they remain graded with availability unknown unless a completed matching
transcript call proves availability. Use a pinned stdio bridge such as
`npx -y mcp-remote@VERSION URL` to obtain handshake and spend evidence.

To repair terminal unavailable cells, keep the original matrix and calibration
and add `--resume --rerun-unavailable`. This invocation selects only previously
unavailable cells, preserves prior bundles in `attempt_run_dirs`, and allocates
fresh attempt IDs. Ordinary resume retains its existing deferred-streak rules.
It does not rewrite historical graded cells whose startup evidence was never
recorded; audit those separately before changing their classifications.

```bash
bin/hq-sew gap run --harness codex --model MODEL_ID --arm floor --arm ceiling --arm native --arm perplexity --reps 3 --wheelhouse /tmp/gap-wheelhouse --provider-mcp-config /tmp/provider-mcp.yaml --run-root /tmp/gap-battery --resume --rerun-unavailable --prewarm-providers
```

Every GAP cell audits tool requests for reads of the resolved catalog/workbench
root, including brief search and reference cells without a workspace profile.
Catalog reads (including `tasks.yaml`, which contains the oracle) mark the cell
`contaminated` and exclude it from grading. Code cells also forbid their hidden
asset path. Unattributed shell-network attempts also contaminate; calls with
attributed denial evidence are counted separately in each cell's
`spawn-metadata.json` as `arm_audit.denied_network_attempts` and do not
contaminate on their own. The local-only pip exemption is a strict allowlist:
a direct `pip`/`pip3`/`python -m pip` `download` or `install` of
`<name>==<version>` pins with only `--no-deps`, `--no-index`, `-q`/`--quiet`,
`-d`/`--dest DIR` and `--find-links` naming the cell's own wheelhouse, under the
enforced offline cell configuration. Shell wrappers, pipes, chains, requirements,
constraints, URLs, combined short options and unknown flags never qualify.
Qualifying calls count in `arm_audit.config_neutralized_network_attempts`; see
`lib/python/sew/gap/WORKSPACE.md` (GAPPIP-01).
Detection and denial attribution share invocation parsing: quoted pip text in
ordinary command arguments is harmless, while recognized wrappers can receive
attributable denials without qualifying for configuration neutralization.
Substitutions inside unquoted shell comments are ignored until the next
newline. Substitution and shell-body parsing share a 64-level nesting limit;
requests reaching it record `workspace:shell-network` contamination without
denial attribution, and the audit returns normally for result publication.
Cell setup strips all supplied `PIP_*` settings and supplies only the driver's
`PIP_NO_INDEX`, `PIP_FIND_LINKS`, `PIP_CONFIG_FILE`,
`PIP_DISABLE_PIP_VERSION_CHECK` and `PIP_REQUIRE_VIRTUALENV`. The exemption
also refuses any additional effective pip setting, including `PIP_REQUIREMENT`
and constraints, even when retained output shows a local success.
Retain the harness tool isolation and sandbox controls
as the prevention boundary.

Calibration excludes contaminated reference attempts before grading and retries
the same arm/repetition in fresh `-att2` / `-att3` bundles. Contamination and
infrastructure retries share a three-attempt total bound; exhaustion publishes
no calibration record. Accepted cells retain `excluded_contaminated_run_dirs`
and their own `denied_network_attempts` count. See
[GAPATTEMPT-01 in WORKSPACE.md](lib/python/sew/gap/WORKSPACE.md#gapattempt-01-information-exposure-and-denied-attempts)
for attribution requirements and the canary-based validity argument.

The JSON projection includes cell count, projected tokens, projected model USD,
per-task/arm rows and exclusions. Reference arms use their own calibration means;
search arms use pooled floor/ceiling means as a proxy, **not a measured search
estimate**. Reference retries are included. Unknown usage or model prices produce
`null`, never zero. Model prices come from SEW's receipted price table (override
with `--price-table`). Provider fees, separate canary invocations and brief judges
are excluded, so model USD is a lower bound on the battery cost. Actual search
work may consume far more tokens than this reference proxy. Task budgets cap
individual jobs, not total battery spend. Retain the calibration bundles for
projection and compare projected versus actual spend after the battery.

MCP child stderr sentinels protect availability only when the harness forwards
that stderr. If both evidence directories are unwritable and stderr is hidden,
a stale parent snapshot can still misclassify discovery. Ready events alone do
not prove launch; first output reduces but does not eliminate asynchronous startup
races. Discovery snapshots remain `unknown` until tools/list finishes or a
handshake error is observed.


## Report and refresh

```bash
bin/hq-sew gap report /tmp/gap-battery
bin/hq-sew gap explain /tmp/gap-battery --task TASK_ID --arm native
bin/hq-sew gap refresh --ecosystem pypi --since 2026-01-01
bin/hq-sew gap recalibrate --harness codex --previous-model OLD_MODEL_ID --model MODEL_ID --reps 5 --wheelhouse /tmp/gap-wheelhouse
bin/hq-sew gap --help
```

Reports read the calibration snapshot and paired run index, and write JSON plus
markdown. Review tokens beside outcomes, pricing exclusions, defects and control
harm before drawing conclusions. Refresh mines candidates and writes one JSON
receipt and GAP-07 authoring prompt per candidate, plus `tickets.json`, under
`gap/refresh/<batch>/` in state. The operator or walker reviews and imports the
tickets into the normal worker pool; refresh never dispatches. Suggested families
need author review. The template retains its seed supply boundary, while each
prompt also states the requested exclusive cutoff. Validate authored fixtures,
review their catalog PR, and fully calibrate the updated catalog before a run.

After a model change, recalibrate requires the explicit previous model, preserves
its record and thresholds, and re-runs only admitted-task floors. Gap tasks retire
strictly above the maximum floor; controls below their minimum are rejected.
Retired/rejected entries remain in the new record without being rerun. Retirement
has a timestamp, reason and previous evidence. Ceiling evidence is inherited and
labelled, not measured on the new model. Use full `gap calibrate` to measure both
references anew, or after changing the catalog. Both live commands require the
quota window and environment described above. `--state-root` selects outside-tree
state; `--export` explicitly exports recalibration evidence for review.

Reports use their embedded calibration snapshot by default. To apply retirement
to a battery on the same model, pass its updated record with `gap report
/tmp/gap-battery --calibration /tmp/current-calibration.json`. Reports refuse model
identity mismatches, exclude retired cells from every aggregate and pairing, and
retain the retired tasks and reasons in the calibration appendix. A model-change
record cannot be applied to a battery run on the previous model.
No routing or worker-class changes follow automatically from bench results.

A battery graded with `SEW_GAP_JUDGES=claude-code` has verdicts but no judge
agreement. Once codex quota is available, add it without regrading:

```bash
bin/hq-sew gap agree /tmp/gap-battery --catalog-root /path/to/the/grading/checkout --harness-auth broker
```

Each succeeded brief cell's stored payload is rebuilt from its saved source
snapshots, so nothing is recaptured. The command refuses a cell unless the
rebuilt payload's sha256 equals the one the primary judged and the stored labels
reproduce the stored verdict; `--catalog-root` must therefore be the checkout
whose `catalogs/gap` graded the run. Only then is the codex judge called. The
primary's verdict never changes. For measured cells, the command repairs a
missing or different `calibration-outcome.json` from `gap-outcome.json` without
calling codex again. This recovers an interrupted write between the two outcome
files. It exits 1 if any codex call failed, so rerunning it retries only those
judges. `gap report` then
adds a judge-agreement table: disputed verdicts in each direction, label
agreement and kappa per arm.

Provider pre-warming retries transient npm network failures up to three attempts
with bounded backoff; persistent package errors fail immediately. Terminal errors
retain full npm stderr. GAP persists provider availability reclassification in
`run.json` as well as the battery state, including for custom harness runners.

DNS qualification was deliberately removed for Codex as well as Claude: ordinary
resolver failure is not attributable sandbox evidence, and raw-IP HTTPS isolates
transport denial from resolver availability. Neither harness's admitted canary
qualifies independent DNS containment; their network sandbox remains the boundary.
Admitted probes carry `attribution`: `sandbox-violation` for Claude's matched
host/port denials and `denial-text` for Codex. The separate bench-owned direct
socket probe carries `harness-runner-permission:codex-sandbox` or
`harness-runner-permission:srt`; historical outer-boundary bundles retain
`bench-seatbelt-permission` and their profile hash. Commands and event shapes
must match the explicitly selected harness. Repeated completed Codex calls
are refused (duplicate deliveries with the same item ID are deduplicated).
Every nonempty sandbox violation line must use recognized wording; CRLF is accepted.

## Historical cost recovery (VENDORPRICE-01)

The 2026-10-03 battery and 2026-10-04 metered GAPMCP rerun's published cost
columns are **lower bounds pending operator re-pricing after merge**. Exa
receipts were hidden by formatted MCP responses; Exa and Parallel tool names
were redacted to `unknown` because their tariffs were empty. The earlier
battery additionally lacked metering for every vendor (METERAVAIL).

For standalone Searchlight, first update to the merged `main` revision; for
the agent-os copy, wait for main-catchup. Preserve the original bundles and
reports and work on a copy outside the checkout. Match each stored provider call to its transcript
tool invocation and response before reconstructing pricing evidence through
`sew.mcp_meter.CallMeter.record` and the updated tariff/price table. Retain
original call identity, timestamps, status, and original evidence; do not
simply assign all redacted calls a search rate. Fetch calls need page counts.
Do not infer Parallel's search mode from an objective or query: use a recorded
mode/connection override, otherwise retain `search_mode_not_observed`.
New records keep call-supplied selectors in `request.pricing_arguments` and
connection-supplied selectors in `response.meter.connection_override`; connection
selectors win for pricing without changing the call's recorded argument keys.
Call-supplied result limits (`numResults` / `max_results`) are retained as positive
integers or `<unrecognized>`; absent limits are omitted. Older records may require
the matching transcript to recover the requested limit.
The wrapper reads Parallel overrides from URL query parameters, `headers` /
`http_headers` mappings, the resolved `header_from_env` mapping, literal
`mcp-remote --header` arguments, and operator-supplied `--header-file` files.
It reads `header_from_env` before spawn creates its header file. `${...}` references
in args are resolved or refused at config load; header secrets must use
`header_from_env`, not an arg reference such as `${SEARCH_CONFIG}`.
URL query parameters override headers.
Only `mode` and `max_results` enter pricing evidence; malformed selectors remain
unpriced and other header content is withheld.
Record the new rate `source` and `as_of`; these are current published rates,
not a claim about historical invoices. Request-based page counts remain
estimates. Keep original unmetered cells and unmatched calls explicitly
unpriced; a missing record cannot be recovered from a count alone.

Regenerate GAP and bake-off reports from the copied evidence, inspect each
vendor's `pricing_coverage`, and publish the corrected report with a recovery
note. Keep lower-bound labels wherever metering or pricing remains incomplete;
do not remove them solely because a vendor now has a tariff. Updating the
published battery reports and their external copies is an operator step.
