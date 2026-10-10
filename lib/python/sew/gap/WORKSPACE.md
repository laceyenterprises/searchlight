# GAP workspace driver contract

GAP-02 adds an opt-in `HarnessRunConfig.workspace_profile=True` for code tasks
loaded from the GAP catalog. Supply the task ID and `wheelhouse=Path(...)`, plus
the existing search arm, provider exposure and live-harness authentication.
`gap_module_root` defaults to the SEW module root; tests can point it at a
synthetic catalog. Production and legacy cells keep their existing surfaces.
The GAP CLI and execution grader belong to GAP-04/11.

The driver snapshots the pristine fixture in parent memory, creates a fresh
workspace and a per-cell Python virtual environment, and copies only the task's
hash-verified `old` and `dependency` wheels into its wheelhouse. `new` wheels
remain verifier-only; verification separately copies all three roles.
`PIP_NO_INDEX=1`, `PIP_FIND_LINKS`,
`PIP_CONFIG_FILE=/dev/null` and `PIP_REQUIRE_VIRTUALENV=1` govern installs.
Cell setup removes all caller-supplied `PIP_*` settings before installing the
driver's five settings (the four above plus `PIP_DISABLE_PIP_VERSION_CHECK=1`).
Parent environment filtering also drops inherited pip settings. Requirements,
constraints, extra indexes and future pip settings therefore cannot silently
add sources. The transcript's local-only pip exemption refuses any effective
`PIP_*` key outside that explicit allowlist, even with truncated success output.
The environment lives outside the source workspace so it is not a deliverable.
Its `bin` directory leads the shell's `PATH`; the canary invokes `python3`
through that path so it runs with the cell interpreter inside the sandbox.
Reading installed package source is allowed.

Every live code cell with a wheelhouse also requires the filesystem containment
described in [GAPWHEEL-01](#gapwheel-01-verifier-cache-containment) below.

Claude Code gets Read, Edit, Write and sandboxed Bash. Codex gets shell tools
and `workspace-write` with command networking disabled. Native search or the
single metered provider MCP is added according to the existing arm contract.
For Claude provider arms, `--tools` retains the built-in `ToolSearch` for deferred
schema discovery; `--mcp-config` supplies the provider and `--allowedTools`
pre-approves `mcp__<server>__*`. `--tools` controls built-ins, not MCP tools,
as specified in the [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
Claude's settings sources are isolated, unsandboxed retries are disabled, and
its shell network allowlist is empty with a global deny. Configuration overrides
that could replace these policies are refused. These settings follow the
[Claude sandbox documentation](https://code.claude.com/docs/en/sandboxing) and
[Codex configuration reference](https://developers.openai.com/codex/config-reference).

Before **each cell**, a separate harness invocation in the same workspace and
with the same tool surface must execute the exact curl, isolated pip-download
and Python HTTPS probes. Only their matching shell tool results count;
both harnesses accept a shell `-c`/`-lc` wrapper containing the exact command.
All three must show an explicit sandbox/network permission denial. Successful
egress, missing tools, timeouts, ordinary DNS errors, extra tool calls, incomplete
evidence and sandbox boot failures all raise `EgressCanaryRefused`; the job never
starts. This is deliberately stricter than merely observing nonzero exits.
`<run-dir>/artifacts/egress-canary.json` retains the evidence, admission flag and
separate canary usage (job metrics remain job-only). No cached admission survives
a change in configuration or binary. Live qualification must run under the target host's
service-account environment; hermetic tests do not certify a live harness.

The transcript audit adds unattributed shell-network attempts and references to hidden/catalog
or workbench paths to the existing out-of-arm search audit. It examines tool
requests, not prose or quoted tool results. The audit recognizes common command
wrappers, offline pip overrides (including empty/quoted `PIP_NO_INDEX` assignments
and disabling or unsetting pip's `no-index` configuration), and direct Python/Node network calls. It is an
attempt detector, not a complete shell or program analyzer; prevention comes
from the canaried harness sandbox. A contaminated job is never a scored success.

`<run-dir>/artifacts/workspace.diff` is a binary-capable Git patch against the
in-memory baseline. A new, parent-owned index includes ignored additions, deletions,
symlinks and executable mode changes. Git metadata, escaping symlinks, special
files, oversized workspaces or patches, invalid UTF-8 evidence and credential-bearing output
are refused instead of producing a partial patch. A diff refusal records the
cell as `failed` with `failure_category: workspace_diff_refused`, logs the
scrubbed refusal in `<run-dir>/artifacts/harness-stderr.txt`, and persists the
sanitized transcript and remaining evidence bundle without
`<run-dir>/artifacts/workspace.diff`.
The existing transcript audit still marks out-of-arm calls as `contaminated`.
Empty directories are not represented by Git. GAP-04 can apply the patch with
`git apply --binary` to a pristine fixture outside the agent workspace. The
canary evidence and any accepted diff are indexed in the evidence bundle;
hidden assets are never copied into the cell.

Both fixture and final snapshots admit at most 4 MiB per regular file, 16 MiB
of regular-file contents in total, and 10,000 entries including directories and
symlinks. Preflight checks sizes before reading; bounded reads also enforce the
captured-byte limits if files grow after preflight. The separate patch cap is
190,000 bytes. Exact credential matching ignores environment values of eight
characters or fewer to avoid treating flags such as `1` as secrets; the
Authorization, bearer-token and Cookie shape checks remain active for every diff.

GAP-03 reference cells use `provider_id="floor"` or `"ceiling"`, with
`native_search_available=False` and no provider exposure. Code references require
`workspace_profile=True`; brief references use the existing deliverable profile
(`False`). Both resolve exclusively from the hash-validated GAP catalog and refuse
prompt overrides. The floor receives the catalog prompt unchanged. The ceiling
appends exactly two newlines, `Reference material:`, a `Source URL: <url>` line,
a `Retrieved date: <date>` line, a blank line, and the verbatim excerpt. No
whitespace is stripped or appended to the excerpt. Both expose no search tools.
`spawn-metadata.json` records `reference_arm` and `oracle_excerpt_sha256` in
`arm_contract`, including the floor's oracle hash for paired calibration provenance.

## GAPWHEEL-01: verifier cache containment

Every live code cell with a wheelhouse passes its resolved cache path into
`bench_network_boundary`. macOS applies a filesystem-only Seatbelt profile to
the entire harness process tree, denying cache reads and writes, including
non-shell Read tools and MCP descendants. Cache and ancestor renames are also
denied so moving a directory cannot bypass the path rule. Linux masks the cache
with an empty read-only mount after all system, runtime and scratch binds.
A cache overlapping scratch is refused. The parent remains outside the boundary
to copy hash-verified permitted wheels and later run verification.

The cell's copied `wheelhouse` remains readable but immutable for the entire
macOS harness tree, including non-shell Write tools and descendants. The profile
denies every file-write operation beneath it and unlinks of its ancestors, so
file creation, content or mode changes, hard-link aliases, and replacing the
directory by renaming it or scratch cannot add unverified pip sources. Linux
continues to bind the copied wheelhouse read-only after writable scratch mounts.
Before macOS admission, a parent-created writable marker in the copied directory
must remain readable inside the exact job boundary while creation, modification,
chmod, hard links, wheelhouse renames and scratch renames all fail with an OS
permission denial. Missing inputs, unexpected errors, malformed or incomplete
evidence and any successful write refuse the cell before model execution.
The marker is removed after qualification. Evidence is retained under
`direct_egress.wheel_cache_isolation.cell_wheelhouse` with per-operation `denied`
flags, backend and `harness_tree_confined`.

All verification scratch, wheel copies, installed packages and hidden tests live
under the fixed per-account `/tmp/sew-gap-verifier-<uid>` root (resolved through
the system `/tmp` alias on macOS). This root is independent of run roots,
`TMPDIR` and state configuration. Every macOS code-cell profile denies the root
and ancestor renames before the harness starts, so it also denies copies created
later by concurrent GAP runs under the same account. Verification stays outside
the agent boundary. The root denial also applies to code controls without a
wheelhouse. Scratch is removed after verification; the empty shared root
persists. A symlink, foreign-owned root or cell scratch overlapping this root
refuses admission. Directory permissions alone do not isolate same-account cells.

Before admission, bench-owned Python processes attempt a direct wheel read and
discover that wheel from the cache's parent directory using only its basename,
then try to read discovered paths. The permitted cell scratch is excluded from
discovery. Both probes use the exact filesystem boundary and runtime mounts of
the job. A source wheel must first be readable by the parent; permission denials
qualify macOS, while absence inside the masked namespace also qualifies Linux.
Readable wheels, missing inputs, malformed evidence, process failure, a traversal
over 10,000 entries or timeout refuse admission. Successful evidence is retained
as `direct_egress.wheel_cache_isolation` in `egress-canary.json`, with backend,
direct/discovery denial and filesystem `harness_tree_confined` fields.

macOS admission additionally copies a wheel and creates an installed-source
marker beneath that shared verifier root, then qualifies direct and discovery
reads of both using the exact cell profile. Discovery starts from the shared
root, without a verifier-run directory or copy path. The parent must read each
probe input first; both inputs are removed after qualification. Failures refuse
admission. Evidence is retained in `wheel_cache_isolation.verifier_copies`, with
separate `wheel` and `installed_source` denial records. A same-account concurrency
regression holds an agent boundary open while real verification installs the new
package, proving denial of the newly created wheel and installed source while
the agent's old inputs remain readable.

The macOS direct runner probe and Codex shell canary also run inside the new
filesystem profile. Claude's out-of-model srt qualification receives the same
prefix while attesting the unwrapped harness arguments. If the outer profile
prevents a nested sandbox from starting, the cell refuses; there is no fallback
to an exposed cache. This filesystem guarantee does not change the reduced
non-shell network confinement described below. Brief cells have no wheel cache
boundary. Transcript auditing of the cache remains additional evidence.
The transcript audit also forbids the shared verifier root for every caller.

## CANARYCLAUDE-01 RCA and denial attribution

The 2026-10-04 08:19Z captured Claude calibration failed safely. Curl to
1.1.1.1 returned exit 7 (connection failure), getaddrinfo returned exit 1
(nodename nor servname provided), and isolated pip returned exit 1 (no matching
distribution). None of these ordinary errors proves containment. The matching
Bash tool result appended a separate `<sandbox_violations>` block with two
`deny network-outbound pypi.org:443 (host is on the deny list)` entries.
Those entries prove only the pip target was denied. The old parser inspected
only each probe's error string, missing Claude's separate denial channel.

Claude uses a filtering proxy plus macOS Seatbelt, with an empty network
allowlist and global deny. The captured transcript shows proxy hostname
denials are reported separately; it provides no attributable denial for raw
IP curl or the resolver. Curl now targets `<nonce>.example.com:443`; the
resolver probe is replaced by Python urllib HTTPS to `<nonce>.example.org:443`.
Pip continues to target pypi.org:443. Distinct hosts prevent one denial from
qualifying another probe; nonce hostnames bind both general HTTPS probes to
the invocation. Curl uses the sandbox filtering proxy rather than bypassing it,
so the hostname is available for the policy denial.

A resolver failure alone cannot distinguish sandbox policy from NXDOMAIN or
host resolver failure. HTTPS instead tests application egress through a
second client while requiring an explicit proxy policy denial. This does not
claim independent DNS containment qualification: Seatbelt and the sandbox
configuration remain the prevention boundary. Each of the three attributed probes must fail with an
integer nonzero exit and its exact host/port denial in the matching Claude
tool result's sandbox block. Missing, mismatched, or ordinary errors without
that block refuse, even when the probe text says permission denied. A present
block with no recognized denial lines reports `unrecognized sandbox_violations format`, so operators can distinguish CLI wording drift from missing evidence.
Codex continues to require explicit denial text in each of the three probe errors;
its curl and Python HTTPS targets use 1.1.1.1 to avoid DNS dependency.
The driver passes the selected harness ID to the canary program.
Assistant claims and unmatched results cannot qualify either harness. Fixture tests
verify parsing, not live harness qualification.

DNS qualification was deliberately removed for Codex as well as Claude: ordinary
resolver failure is not attributable sandbox evidence, and raw-IP HTTPS isolates
transport denial from resolver availability. Neither harness's admitted canary
qualifies independent DNS containment; their network sandbox remains the boundary.
Admitted probes carry `attribution`: `sandbox-violation` for Claude's matched
host/port denials, `denial-text` for Codex. Commands and event
shapes must match the explicitly selected harness. Repeated completed Codex calls
are refused (duplicate deliveries with the same item ID are deduplicated).
Every nonempty sandbox violation line must use recognized wording; CRLF is accepted.


## CANARYBYPASS-01 RCA and bench-owned containment

CANARYCLAUDE-01 put a direct proxy bypass command in the agent-run canary.
The 2026-10-04 captured run d97d492dcaf04c27902d6c79365db5dc was stopped by
Opus 5.5's safety classifier, then refused by its Opus 4.8 fallback. Earlier
three-probe run f509c351d9b648cc83f039416a0234b5 had executed. This was a
boundary ownership error: asking the evaluated model to test bypass made
containment qualification depend on willingness to perform the probe.
The sanitized captured refusal is a regression fixture. Refusals remain cell
refusals; no probe is reworded or retried to overcome a safety refusal.

CANARYBYPASS-01 originally implemented the second design option: an additional
bench-owned macOS Seatbelt profile around the direct probe and both harness
process trees. CANARYNEST-01 removes that outer profile because it prevented the
harness's inner shell sandbox from starting. The replacement below records the
current implementation, not operator approval to relax the original guarantee.
An explicit operator decision accepting non-shell egress, or a compatible
replacement harness-tree boundary, remains required.

The agent prompt retains only the three existing probes for both harnesses.
SEW runs the direct socket probe outside the model under a separate sandbox
runner, connecting to 1.1.1.1:443 and requiring EPERM or EACCES. Success,
timeouts, routing errors, malformed output, missing runners and probe process
failure refuse. Codex uses its installed `sandbox` command (or `sandbox macos`
when advertised), requesting the cell's sandbox mode and inheriting the isolated
`CODEX_HOME` unchanged. The config must explicitly disable workspace-write
networking; invalid policy and configuration/profile overrides refuse rather
than being masked with a network override.

Claude's probe uses separately provisioned `@anthropic-ai/sandbox-runtime`
(`srt`, pinned to 0.0.78 in the runbook), with the cell's deny-all network
settings. The probe filesystem policy is bench-synthesized, allowing writes to
the workspace and probe scratch directory. Claude Code has a separate bundled
sandbox runtime. Recording both CLI versions does not establish runtime parity;
the bundled runtime version remains unknown. Only the agent-run shell canary
exercises the actual cell's Claude sandbox.

SEW starts a parent-owned local HTTP/CONNECT proxy before qualification. It
passes uppercase/lowercase HTTP, HTTPS and ALL proxy variables, and replaces
NO_PROXY with the local-only bypass list. The proxy accepts only the selected
harness endpoint host/port pairs, using DNS answers frozen before launch.
Defaults are Anthropic:443 for Claude and OpenAI/ChatGPT:443 for Codex; a
configured base URL replaces them, preserving its explicit port or defaulting
to HTTPS:443 / HTTP:80. Invalid URLs or failed resolution refuse. The direct
probe target cannot be an endpoint exception. The proxy relays HTTP requests
and opaque CONNECT streams without logging credentials and closes on success,
refusal, cancellation or exceptions.

**The proxy is only advisory outside the shell sandbox.** The harness process,
MCP server descendants, WebFetch and harness HTTP clients are not wrapped in a
bench-owned OS network boundary. Clients that ignore proxy variables can reach
arbitrary external hosts directly. The sibling direct probe cannot qualify
those paths. External provider transports therefore no longer fail closed.
An admitted cell proves the recorded runner and shell probe denials, not
harness-tree confinement. This loss of the original second design option is
the unresolved containment decision described above.

The artifact retains runner-specific direct attribution
(`harness-runner-permission:codex-sandbox` or
`harness-runner-permission:srt`), runner path/version, requested `policy`
and `policy_scope: requested-runner-policy`, `harness_tree_confined: false`,
endpoint addresses and host/port authorities. Claude records `harness_version`,
`bundled_sandbox_runtime_version: null` and
`runtime_relationship: standalone-srt-not-bundled-claude-runtime`.
The network records have no `profile_sha256`; historical outer-boundary records
retain that hash and `bench-seatbelt-permission` attribution. Failed boundary
preflight leaves an inadmissible artifact with unproven attribution.
Requested policy metadata is not a measurement of the applied runner policy.

Residual risk includes arbitrary non-shell egress, opaque CONNECT streams and
trusted endpoint or loopback relays. The inner harness shell sandbox and its
three attributed probes retain their tighter shell policy. Endpoint address
changes after resolution can break proxy-aware transport; no dynamic allowlist
expansion occurs. Hermetic tests cover runner discovery, version evidence,
rejection of network-enabled Codex config, transient process retries, startup
failure classification in both output streams, permission-denial admission and
preserved harness arguments inside the filesystem wrapper even without a cache.
GAPWHEEL-01 now adds a separate filesystem-only outer profile and refusal on nested startup
failure. Proxy tests cover HTTP/CONNECT relay, custom ports,
denied authorities, address fallback and teardown. The generated outer-profile
compilation and real Seatbelt direct-egress tests were removed with that
profile; remaining tests do not certify a live authenticated harness.

## GAPATTEMPT-01: information exposure and denied attempts

A network request denied before connection supplies no external response, package,
or documentation to the evaluated agent. Under the mandatory fresh canary above
(all three shell probes attributable, plus the separate runner's direct denial),
an attributable shell denial is behavior evidence rather than information
contamination. Admission still requires the canary. This audit covers matched
shell calls only; it neither establishes sandbox health nor detects information
exposure through unconfined MCP servers, WebFetch or harness HTTP clients.
The proxy's documented endpoint and loopback exceptions remain trusted.

The audit pairs Claude tool results by tool-use ID and Codex `item.completed`
shell results by item ID with the exact requested command. Codex result items
may have status `completed` or `failed`; a nonzero integer `exit_code` or
`failed` status supplies the failure signal, never denial evidence by itself.
Both detection and attribution use the same quote-aware invocation parser,
recursing into `bash`/`sh`/`zsh` command bodies (including `-i -c` and `-- -c`)
and `eval`, and recognizing simple `nohup`, `time` and `xargs` wrappers.
Pip global flags and versioned executables use the same parsing for detection
and default PyPI denial targets. Literal arguments to `echo`, `printf` and `rg`
are not executable requests. Unquoted shell comments at word boundaries are
ignored through the next newline, including any substitutions in the comment;
quoted or escaped `#` characters remain ordinary argument text.
Substitution scanning and recursive shell-body parsing share a 64-level depth
limit. Requests reaching the limit are uncertifiable and contaminate as
`workspace:shell-network`, without denial attribution or an audit exception,
so the runner can still publish its terminal evidence bundle.
Active command substitutions are detected, but
cannot receive denial attribution because the outer command can replace their
output or exit status. A wrapped command with local
setup or a local pipeline still has one network invocation; two network
invocations or malformed quoting cannot be certified by one denial.
Recognized sandbox violation blocks must cover the command's target host/port;
a failed result can also prove a network/socket permission denial. Missing results, ordinary
connection errors, assistant claims, unrelated denials, and commands with multiple
network invocations remain contamination unless the GAPPIP configuration exemption
below applies. Background completion requires matched denial evidence or the
GAPPIP background-output attribution below. Catalog, hidden asset, oracle and
workbench reads continue to contaminate even beside a denied network request.
`arm_audit.denied_network_attempts` counts distinct attributed calls per cell;
calibration records and GAP JSON/Markdown reports expose the metric separately
from scoring. Older bundles without the metric report zero recorded attempts.

Calibration excludes contaminated references before grading and retries the same
arm/repetition in a fresh cell directory. The existing total limit of three
attempts per reference includes infrastructure retries and contamination retries.
A third contaminated attempt aborts with `contamination retry bound exceeded`;
no partial calibration record is published. Accepted cells retain all attempt
paths and `excluded_contaminated_run_dirs`, so excluded evidence and its cost
remain available without entering admission pass rates.

Denial attribution requires a unique, separate trailing sandbox block or a failed
call whose final nonempty line reports network permission denial. Commands with
non-network segments after the network invocation remain contaminated, including
pipelines that can replace output or exit status. Malformed tool inputs do not
crash attribution. These structural checks do not authenticate arbitrary stdout.
## GAPPIP-01: configuration-neutralized pip attempts

The audit can exempt a detected pip network attempt only through a strict
allowlist (STRICTPIP-01). It does not parse shell syntax: shell syntax is
open-ended, and earlier command-text parsing was bypassed by requirements files
carrying a remote `--find-links`, combined short options and `bash -i -c` /
`bash -- -c` wrappers. A shell call is exempt only when all of these hold:

1. **Direct invocation.** The command text is exactly the canonical
   `shlex.join` of its words, so it has no wrapper (`bash`/`sh`/`zsh -c` with
   any options, `env`, `xargs`, `eval`, `nohup`, `time`), subshell, expansion,
   command substitution, redirection, comment, pipe or `;`/`&&`/`||` chain.
   Equivalent shell spellings with extra whitespace or different quoting are
   not canonical and therefore do not qualify for the exemption.
2. **Executable.** `pip`, `pip3`, `python -m pip` or `python3 -m pip`, by bare
   name with nothing before the subcommand.
3. **Subcommand.** `download` or `install`.
4. **Packages.** One or more `<name>==<version>` pins with a valid version.
5. **Flags.** Only `--no-deps`, `--no-index`, `-q`/`--quiet` (uncombined),
   `-d DIR`/`--dest DIR` (download only, separate value, no URL) and
   `--find-links` followed by exactly the cell's `PIP_FIND_LINKS` wheelhouse.
6. **Environment.** The enforced cell configuration has `PIP_NO_INDEX=1`,
   `PIP_CONFIG_FILE=/dev/null`, only allowlisted `PIP_*` settings and a local
   `PIP_FIND_LINKS`.

Anything else is not exempt, including requirements (`-r`), constraints (`-c`),
editables (`-e`), index and trusted-host options, `--config-settings`, any URL,
combined short options, `=`-attached values, abbreviations and unknown flags.
Such a detected attempt contaminates unless the sandbox recorded an attributable
denial, which counts in `denied_network_attempts`. Pip download requests, and
pip installs naming a URL, are detected through the shared invocation parser,
including the wrappers described in GAPATTEMPT-01; quoted command text in
ordinary literal arguments does not count as an attempt. Recognizing a wrapper
for detection or denial attribution never makes it eligible for configuration
neutralization. Ordinary offline installs that the parser does not classify
as network attempts, such as `pip install -e .`, are unchanged. Codex reports
commands through a `-lc` shell wrapper, so its pip downloads never qualify.

Claude background evidence requires a `task_notification` tying its
`tool_use_id` to an `output_file`, plus a paired tool result for the exact
`cat <output_file>` request. Unrelated output or assistant claims do not qualify.
Visible HTTP(S), retry or connection output rejects certification. This output
check is advisory: a pipeline such as `| tail` may hide network lines, and pip
success alone does not prove a wheelhouse source. The enforced environment and
override and indirect-source rejection supply the local-only basis; the output
only confirms a completed pip result.

`arm_audit.config_neutralized_network_attempts` counts distinct detected
shell-network calls accepted by this exemption, separately from denials and
scoring. Calls with attributed denials count only in `denied_network_attempts`.
Calibration retains the accepted cell's count and sums it in its summary;
excluded attempt bundles retain their own audit counts. GAP JSON and Markdown
expose the metric for included and excluded cells. Legacy fields default to zero.

## Portable backend selection (SWX-04)

`sew.yaml` accepts `sandbox: auto` (default), `seatbelt`, or `bubblewrap`.
Auto selects `/usr/bin/sandbox-exec` on macOS and `bwrap` on Linux. An explicit
backend on the wrong OS, a missing executable, failed namespace setup, or an
inadmissible canary refuses GAP code cells with the reason. Brief cells do not
enter this boundary. There is no unsandboxed fallback.

On macOS, standalone runner qualification follows the contract above. On Linux,
bubblewrap creates a fresh filesystem, PID and network namespace with
`--unshare-net`, drops capabilities, binds the cell scratch read-write, and
rebinds its copied wheelhouse read-only. Verifier test invocations instead bind
only the workspace and report directory writable, retaining read-only tooling.
Only system runtime directories and explicitly selected runtime/auth inputs are
visible; host `/tmp`, `/run`, process trees and arbitrary home files are absent.

A bench-owned Linux canary runs curl and isolated pip against a raw external IP,
plus direct TCP and a UDP DNS datagram. Each must fail: TCP and DNS require
EPERM/EACCES or the isolated namespace's ENETUNREACH; curl and pip require network
denial text. Missing programs, timeouts, malformed results, successful probes,
and ordinary resolver failures do not qualify. This DNS check proves external
DNS transport denial, not behavior of every system resolver API. The existing
three-probe Codex harness canary (curl, pip, HTTPS) still runs before the task
and must satisfy its original audit. Linux Claude code cells are unsupported
under CANARYOOM-01 below, including with broker auth; backend qualification
alone does not admit them.

Model transport crosses the Linux namespace only via a private Unix socket to
the existing parent-owned endpoint proxy. A per-invocation loopback relay forwards
HTTP/CONNECT to that socket; the parent enforces the same frozen host/port and
address allowlist as macOS. The relay stays in the harness process group and is
terminated with it. No host network namespace or host loopback is shared. Native
search and external provider transports must honor these restrictions; an
unavailable transport fails closed. Permitted model endpoints and
any explicitly exposed auth/runtime inputs are trusted exceptions; this does
not constrain paths within the opaque TLS tunnel.

`sew doctor` runs a local credential-free backend canary and reports the selected
backend and verdict. Doctor qualification does not replace the mandatory shell
canary in an actual GAP cell. Linux CI installs bubblewrap and requires the real
canary to deny egress; namespace setup failures cannot be skipped there.


## CANARYOOM-01: Claude qualification outside the model

Operator decision (2026-10-04): "Out-of-model qualification". For Claude Code,
this supersedes the agent-run qualification described above. This path supports
macOS only. Linux Claude code cells are explicitly refused, including broker
cells, before a model turn: standalone host srt cannot qualify the cell's
bubblewrap namespace. Use Codex for Linux code cells; Claude brief cells are
unaffected. Before each supported code cell, SEW runs the unchanged curl, pip
and HTTPS program through pinned srt 0.0.78,
with the generated cell network settings, plus the existing direct-egress probe.
Each application probe must fail and have its exact nonce-bound host/port in
srt's filtering-proxy denial records; the direct probe requires EPERM/EACCES.
Successful egress and ordinary connection failures refuse admission.

SEW checks the unwrapped Claude argv and attests sandbox enabled,
`failIfUnavailable: true`, empty network allowlist, global deny,
`strictAllowlist: true`, `allowLocalBinding: false`, `allowAllUnixSockets: false`,
unsandboxed commands disabled, no excluded commands, Claude Code 2.1.282 and
srt 0.0.78.
Any mismatch refuses. Version updates require an explicit pin change and renewed
live validation. No Claude model turn participates in qualification, so a model
refusal cannot change admission. Probe text and infrastructure retry policy stay
unchanged; neither is adjusted to evade a safety classifier. Codex retains its
agent-run canary and bench-owned direct probe.

Qualification uses a 75-second per-attempt timeout, above the three sequential
20-second probe limits, and a separate process group. Timeout kills and reaps
the group before the existing bounded infrastructure retry. Every refusal,
including settings/version discovery and retry exhaustion, retains a scrubbed
reason (at most 500 characters), the known direct-egress evidence and available
attestation in `egress-canary.json`; success and refusal use the artifact cap.

The security argument trusts Claude Code to apply the attested configuration.
Standalone srt is not Claude's bundled runtime, and qualification does not prove
runtime parity. This residual risk is mitigated by per-cell attestation and the
retained transcript audit. GAPATTEMPT still contaminates only unattributed
network attempts; attributable denials remain behavior evidence. Non-shell
transports retain the reduced confinement guarantees documented above.


## OHM-08: OSS harness code cells

Hermes, Opencode and Pi have no shell sandbox of their own, so their registry
entries select `code_cell_sandbox: portable`: the bench's own sandbox is the
runner, and it confines the entire harness process tree, including the shell
tool, file tools and MCP descendants. A code cell requires a `litellm/<route>`
model and LiteLLM auth; the only model endpoint is the LiteLLM authority, never
the provider hosts behind it. The adapters enable the shell and file tools only
for code cells (Hermes `terminal` and `file`; Opencode `bash`, `read`, `write`,
`edit`, `glob`, `grep` and `list`; Pi's default `read`, `bash`, `edit` and
`write`). Each reads its endpoint from `SEW_LITELLM_BASE_URL` (Hermes `${...}`,
Opencode `{env:...}`, Pi `baseUrlEnv`), so the Linux bridge can rewrite a
loopback endpoint per launch. Search cells keep the literal endpoint and the
shell off.

macOS refuses a nested Seatbelt profile, so the hosted harnesses' outer
files-only profile cannot also host an inner shell sandbox here. Instead the
OSS harness tree runs under one profile: the unchanged files-only rules, then
only denials and the permitted peers. Writes outside the cell scratch (other
than `/dev/null`, `/dev/tty`, pseudo-terminals and similar devices) are denied. All outbound
network is denied except `localhost:<port>` for the bench endpoint proxy and,
when LiteLLM is loopback, its port. DNS, other loopback services and Unix
sockets are unreachable. `HOME` and `TMPDIR` move into the scratch unless the
arm already isolated them (Opencode's `HOME`). Linux keeps the existing
bubblewrap namespace and bridge, with the same `HOME`/`TMPDIR` move. A Python
console-script launcher (Hermes) also gets its virtual environment and base
interpreter bound read-only, never a tree containing the account's home.

Before any model turn, the bench qualifies the exact boundary: the direct
probe must see EPERM/EACCES under the harness-tree profile (macOS; Linux keeps
its backend canary), and a bench process inside the boundary writes to a
parent-chosen path outside the scratch. Admission requires that nothing
reaches the host there, whether the write was denied (macOS, read-only Linux
binds) or landed in the namespace's private `/tmp`. Evidence is retained as
`runner_id: bench-seatbelt`, `policy_scope: harness-tree-profile`,
`harness_tree_confined: true` and `write_escape`.

The agent-run canary is then mandatory, as for Codex. OSS harnesses run
`PORTABLE_CANARY_PROGRAM`: the same curl, pip download and HTTPS probes, aimed
at `1.1.1.1` (never a name), with `curl -v` and `pip -vv` so the output
carries the denial: the OS refusing a direct connect, or the endpoint proxy's
`403 Permission denied: endpoint not allowlisted` for a proxied one. Each probe
must fail with denial text, as for Codex. The hosted canary program is
unchanged. Hermes takes its prompt in argv, so the canary launch carries the
canary prompt there, and its session ledger is removed before the task so the
task transcript never replays the canary.

The canary and transcript audit read each harness's event shape: Hermes
`tool_call` rows and JSON `terminal` results, Opencode `tool_use` parts and
Pi `toolCall` blocks with `toolResult` messages. Canary validation uses completed
Pi assistant calls from `message_end` and `turn_end`, deduplicated by call ID;
older releases' cumulative assistant `message_start`/`message_update` snapshots
carry incomplete arguments and do not qualify a call. Additional completed
calls or completed calls outside the exact probe still refuse admission.
Tool-result start/end repeats count once,
and Pi's appended `Command exited with code N` status is removed so the
command's own last line is read, as for Codex. `terminal` joins the shell-tool
list; Opencode and Pi call theirs `bash`. Hermes `read_file`, `write_file`,
`patch` and `search_files`, and Opencode `read`, `write`, `edit`, `grep`, `glob`
and `list` join the forbidden-read audit. The audit checks their path arguments
(including Opencode `filePath` and paths in `glob` patterns), resolving relative
paths against the cell workspace. Searching or listing forbidden catalog,
hidden asset, wheel-cache or verifier paths contaminates the cell just as a read
does, even when the OS allows the request.
Denial attribution and the strict pip allowlist are unchanged; tests assert
that each OSS shape audits exactly as the same Codex command does.

Residual risk: a provider arm's MCP server runs inside the same fence, so on
both hosts it cannot reach its provider (as for hosted Linux cells); provider
arms in OSS code cells will report tool failures until a separate decision
admits provider endpoints. A remote (non-loopback) LiteLLM is reached only
through the endpoint proxy, so a client that ignores proxy variables (Node's
default fetch) fails closed. No live OSS code cell has run; offline tests use
fake harness binaries under the real Seatbelt or bubblewrap boundary and a
local LiteLLM stub.
