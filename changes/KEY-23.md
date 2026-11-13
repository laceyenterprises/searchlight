# KEY-23 — Brave migration fence

Spec: governed-credential-registry-phase2@77977e8d0288.
Pure switch_provider prepares a Brave lease reference plus broker-only migration
fence. pause_provider removes delivery while keeping that fence: missing leases
refuse before direct provider readers. Other providers remain pending. Agent OS
migration receipts own expected revisions, operator verification, canary and
rollback expiry. KEY-25 publishes config and performs owner/domain-correct
restart/health under existing authority; no worker activates or deletes keys.

Final remediation accepts matching string lease shorthand, normalizes only
absent/null mapping sections, and refuses malformed section values without
echoing them. Pause still retains the broker-only fence. Legacy environment
initialization is serialized per resolver and remains behind the governed
credential checks. Regression tests cover YAML nulls, reconciliation conflicts,
other-provider preservation and concurrent environment initialization.
