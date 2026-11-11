# BROKERSDKMODEL-01

`sew insight grade` and `sew run-live-harness` accept
`--claude-broker-provider claude-code-corp` (or `claude-code`). Claude judge and
live-arm broker requests now pass the pinned model and optional provider through
the host adapter to the SDK. Omission keeps the existing provider selection;
standalone account auth is unchanged. The updated agent-os SDK host is required.

Grading extractor/judge descriptors record `auth_source` and `broker_provider`
after calls finish, using broker receipt metadata without credentials. Existing
cached grades keep their original descriptors until new model work occurs.

This change is stacked on `claude-code/insight-bench` at
`efcbc9336f7ecfe7bdc828c6631ca17eb91b5e9b` and must merge after that work.
The agent-os BROKERSDKMODEL-01 operator plan owns SRE acceptance after the SDK
deploy and Searchlight float: corporate grade without selector environment
variables, safe grading metadata, and a named entitlement refusal. No live
broker/model calls are part of this change's tests.
