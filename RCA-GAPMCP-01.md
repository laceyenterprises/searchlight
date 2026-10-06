# GAPMCP-01 — Provider discovery is an availability gate

Severity: SEV2, benchmark validity. Incident: 2026-10-03 GAP battery.
Related evidence: PR #7641 and
"docs/postmortems/SEV2-a-search-arm-whose-provider-tool-never-loaded-is-graded-as-that-providers-failure-2026-10-04.md"
(not present in this checkout when the fix was prepared).

## Impact and evidence boundary

The dispatch records 31 of Codex's 108 provider-arm cells making zero search
calls, all graded failures, versus 0 of Claude Code's 126. The affected counts
were Parallel 12/18, Firecrawl 5, Tavily 5, Perplexity 5, Brave 3, and Exa 1.
Those transcripts reportedly attempted `list_mcp_resources`, received no
resources, and answered without provider tools. These are supplied incident
facts, not a fresh replay of the historical battery. Empty resources alone
do not prove tool absence: MCP resources and tools are distinct catalogs.

The code defect and timeout mechanism were reproduced locally. The precise
startup cause of each historical cell remains unproven without its stderr and
handshake timing. In particular, this fix does not reinterpret every zero-call
answer as an unavailable provider.

## Mechanism

`sew.arms.prepare_arm_spawn` builds a private Codex "CODEX_HOME/config.toml".
`_toml_mcp_server` previously emitted no startup timeout and rejected a supplied
`startup_timeout_sec` key. `sew.live_harness._metered` wraps stdio server configs
in `python -m sew.mcp_meter -- <original command and args>`; the shared
`cwp_dispatch.mcp_metering.serve` launches that command and relays JSON-RPC.
Thus an npx cold start, including `mcp-remote` connecting to a hosted endpoint,
is inside Codex's MCP startup window.

OpenAI's [configuration schema](https://learn.chatgpt.com/docs/config-schema.json)
defines `RawMcpServerConfig.startup_timeout_sec` as a number. Its
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
describes a 10-second default and `required` startup semantics. The installed
Codex 0.159.1 instead **reported a 30-second default in the reproduction**;
the installed diagnostic is authoritative for that binary. An initial
11-second fixture was ready under its default, so it was not a reproducer.
With a 35-second fixture, the default reported:

> MCP client for `fixture` timed out after 30 seconds.

The same server reached `ready` with `startup_timeout_sec = 120`, and the relay
persisted successful initialize and tools/list evidence. These tests use
`codex app-server`, initialize, and thread/start only: no model turn, account
credential, hosted provider request, or 1Password access. Reproduce explicitly:

```bash
SEW_CODEX_STARTUP_REPRO_BIN=/path/to/codex python3 -m pytest modules/search-evaluation-workbench/tests/test_provider_availability.py -k real_codex -q -s
```

The old meter observed only "tools/call". The GAP runner treated a harness's
completed answer plus a clean arm audit as sufficient to grade. Neither
established that the arm's provider tools had loaded. This converted a
harness/provider startup failure into a provider quality failure and diluted
that arm's pass-rate denominator.

## Correction

- The meter tracks correlated harness initialize/tools/list requests and
  successful replies, plus listed tool count, in
  "provider-calls/availability.json". Atomic snapshots are written at each
  handshake boundary, including an initial false snapshot before launch;
  termination does not depend on a final flush. No names, descriptions,
  arguments, responses, or errors from the server enter this artifact.
- GAP checks this evidence before invoking a verifier. Live bundle creation
  applies the same gate for the generic suite runner, whose adoption path also
  checks older unindexed live bundles. Missing/failed/empty discovery becomes
  `provider_unavailable`; successful discovery followed by zero search calls
  remains gradable. Contamination, cancellation, spent budgets, and explicit
  harness authentication/spawn errors retain their own classifications.
- Existing unavailable streak, deferral cap, and retry behavior remain in
  force. GAP's report displays availability coverage per arm and continues
  excluding unavailable cells from quality rates and paired comparisons.
- Codex provider configs receive a 120-second startup allowance and
  `required = true`, so optional-server grace cannot start the arm prematurely.
  The cell's independent wall-clock budget still caps execution.
- Optional `--prewarm-providers` resolves only exact pinned npx packages using
  npm's cache. It starts no provider entrypoint, disables install scripts,
  excludes provider-key environment variables, and fails on an unpinned config
  or resolution error.
- `gap run --resume --rerun-unavailable` selects only previously unavailable
  cells, retaining the same matrix/calibration and prior attempt bundle paths.
  Completed available cells and not-yet-started cells are untouched by that
  repair invocation.

Direct URL configs and missing metering-core installations cannot supply
stdio handshake evidence. They are conservatively unavailable for grading;
use the stdio bridge for remote providers. Existing graded historical cells
are not silently migrated: without startup evidence, zero calls do not establish
the correct classification. A full live battery rerun needs the existing
operator quota window; this PR opens for review without performing it.

## Acceptance evidence

Offline fixtures cover never-initialize servers, slow successful discovery
without calls, invalid/empty tools/list, secret-free evidence, Codex timeout
config, pre-warm pins, report coverage and denominators, unavailable-only
resume, and preservation of prior bundles. Both Codex and Claude Code harness
fixtures exercise the live bundle gate; the generic suite regression asserts
unavailable when its harness never launches the configured server.

The opt-in installed-Codex reproduction passed both default-failure and
120-second-ready cases. The required `test-hq-app-standup.sh` passed using its
local remotes and mocked GitHub surfaces. Targeted Python results and managed
pre-push CI evidence are recorded in the PR body.
With the original `live_harness.py` restored in an isolated module copy, both
never-initialize regression cases failed: the old driver returned `succeeded`
for Codex and Claude Code. With the fix, the targeted Python suite recorded
356 passed and 4 opt-in/platform skips; the installed-Codex reproduction passed
separately (2 cases).

After merge and main-catchup, operators should regenerate the GAP report for
newly classified cells, then use the documented unavailable-only repair command
under the normal live quota and service-account environment. This change
requires no daemon bounce, GitHub App/label/repository mutation, or secret change.
