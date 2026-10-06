# METERAVAIL-01: observation failure became provider failure

At 2026-10-04 04:21Z, GAPMCP-01 classified three exported-workbench GAP cells
as `provider_unavailable`. Read-only inspection of the supplied incident bundles
confirmed completed MCP calls: exa repetition 2 had seven; parallel-web
repetitions 1 and 2 had two each. Each availability record had all handshake
flags false and zero tools. The parallel-web repetition 1 attempt 3 comparison
had two completed calls and successful discovery of two tools.

Codex launches MCP children with a restricted environment. The wrapper supplied
only SEW's library on PYTHONPATH; the shared core was discoverable in the parent
but unavailable to the child when SEW was copied outside the checkout. The
child became the vendor server without recording calls. Its stderr warning was
not durable cell evidence. Both gates interpreted the parent's prewritten
all-false record as provider failure. Observation failure also left vendor spend
unknown across the original battery; the supplied dispatch reports empty call
records on both harnesses. This change does not reconstruct missing spend or
claim a new live battery validates it.

The wrapper now passes the resolved core import root explicitly. Availability
records distinguish observed sessions from core-unavailable, URL transport, and
wrappers that never engaged. Only observed failed discovery excludes a cell;
unobserved cells grade, with completed matching Codex MCP items or paired
non-error Claude tool results proving availability and other cases remaining
unknown. This explicitly reverses the earlier exclusion of a wrapper that never
launches: `wrapper_not_engaged` without calls is unknown and graded, so a wrapper
import/launch failure can affect the arm's pass rate. Inspect `unknown_n` and
observation reasons alongside pass rates when diagnosing infrastructure failures.
Crash-recovery adoption preserves the same status as fresh-run grading. Legacy all-false records are
unobserved, while legacy initialize requests remain observed evidence. GAP and
bake-off aggregates expose metering coverage independently of provider availability.

Four new fixture regressions failed against the original meter: exported tree
with stripped child environment, unknown availability without completed calls,
available with completed calls, and durable core-unavailable evidence. The
exported-tree regression exercises discovery and a recorded provider call.
Additional tests cover grading through GAP and coverage in both reports. No live
providers, harnesses, credentials, or worker-pool test suite are required.

The final remediation also removes meter bootstrap Python roots from vendor
children in the ordinary relay and unavailable-core fallback. Other provider
environment settings remain intact; the wrapper keeps its own import path.


## 2026-10-04 addendum: installed wrappers and harness startup

METERREGRESS-01 restores exclusion for an installed wrapper that never starts
its server, with a startup guard. The parent sets `wrapper_engaged = true` and
`observation_reason = server_not_launched` before spawning the CLI; this alone
cannot establish provider failure. Without observed discovery or completed
matching calls, exclusion requires a succeeded harness status or a durable
`harness_ready` / `first_output_token` marker. Early CLI exits and silent boot
hangs retain their harness failure status and unknown availability. Fresh live
creation supplies captured startup/transcript evidence; GAP, adoption and reports
read the persisted bundle evidence through the same resolver.

Completed matching calls override a stale or failed discovery snapshot, including
an engaged parent record the child could not overwrite. URL transports,
core-unavailable relays and uninstalled wrappers without completed calls remain
unknown and graded. The stored `availability` is only a discovery snapshot;
`provider_available` incorporates transcript and startup evidence and remains
the authoritative verdict. This narrows the earlier never-launch reversal while
preserving its protection against excluding harness boot failures. Regression
fixtures cover early exits and no-first-output failures for both harnesses,
pre-ready GAP/adoption failures, post-startup missing discovery, and completed
Codex/Claude calls with an engaged stale snapshot.
