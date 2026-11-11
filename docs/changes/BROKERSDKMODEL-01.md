# BROKERSDKMODEL-01

`sew insight run`, `sew insight grade` and `sew run-live-harness` accept
`--claude-broker-provider claude-code-corp` (or `claude-code`). Claude judge and
live-arm broker requests now pass the pinned model and optional provider through
the host adapter to the SDK. Omission keeps the existing provider selection;
standalone account auth is unchanged. Deploy the updated agent-os SDK host **before**
floating Searchlight. An older host receives a clear model/provider-selector error;
selectors never silently fall back.

Grading extractor/judge descriptors record `auth_source` and `broker_provider`
after calls finish, using broker receipt metadata without credentials. Requested providers
must match the broker receipt. Provider selectors require broker auth and Claude Code;
broker insight grading requires pinned models.
Cached grades freeze their requested model/auth identity and refuse changed identities;
a no-call resume keeps the original receipt descriptors. Reproduction notes carry broker flags.

The final comment round also corrects variant profile rationale and fairness rules.
Async task POSTs make one attempt and retain an estimate after ambiguous timeout/5xx
responses; operators reconcile vendor state before resuming. Grading counts calls under
a lock and saves descriptors even if an unexpected error aborts a step.

This change is stacked on `claude-code/insight-bench` at
`efcbc9336f7ecfe7bdc828c6631ca17eb91b5e9b` and must merge after that work.
The agent-os BROKERSDKMODEL-01 operator plan owns SRE acceptance after the SDK
deploy and Searchlight float: corporate grade without selector environment
variables, safe grading metadata, and a named entitlement refusal. No live
broker/model calls are part of this change's tests.
