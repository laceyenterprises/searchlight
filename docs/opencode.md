# Opencode search harness

Searchlight supports Opencode **1.17.3 or newer**, with an explicit
`litellm/<route>` model from the OSS catalog. `sew doctor` checks the binary
version and reports `version check failed` if the command exits unsuccessfully;
`SEW_OPENCODE_BIN` can select a different executable. OSS support is
off by default. Configure `oss.enabled` and LiteLLM as described in the
[OSS configuration guide](../README.md).

Doctor invokes the executable resolved from the supplied PATH and parses the
last non-empty stdout line as its version, allowing preceding CLI warnings.
An explicitly empty `SEW_OPENCODE_BIN` disables binary discovery in doctor;
only an unset override falls back to `opencode`.

Inspect a plan without resolving credentials or starting a model:

```sh
sew run-live-harness --harness opencode --model litellm/glm-5.2 --arm exa --dry-run
```

Each cell writes `opencode/opencode.json` in its temporary cell directory.
The LiteLLM provider uses `@ai-sdk/openai-compatible` and reads its key from
the child environment. MCP environment values also use environment references,
so credentials are not embedded in this config. HOME and all XDG directories
are isolated in private (0700) cell directories, including `XDG_RUNTIME_DIR`
at `opencode/run`, which replaces the inherited host runtime directory.
Project configs are disabled, and `--pure` disables external
plugins. The only model provider is Searchlight LiteLLM.
Directory setup reuses existing cell directories and rewrites the config, so
setup can be retried in the same temporary directory after a partial failure.

Provider arms expose only their local MCP server. Built-in web, shell,
filesystem and delegation tools are denied. No-search exposes no tools.
Opencode's [local MCP configuration](https://opencode.ai/docs/mcp-servers/#local)
uses a single `command` array containing the executable followed by its arguments.
The adapter converts Searchlight's separate `command` and `args` fields to that
array; Opencode splits it before creating the stdio transport.
The native arm uses Opencode's client-side `websearch` and `webfetch`, with
`OPENCODE_ENABLE_EXA=true` to make native search available for a compatible
provider; shell remains denied. Native search uses Opencode's own search
service, independently of the arm MCP servers.

The invocation is `opencode run --pure --format json --model
searchlight-litellm/<route>`, with the prompt on stdin. JSON `step_start`,
`text`, `tool_use`, `step_finish` and `error` events drive readiness, the final
answer, tool auditing, token budgets and catalog pricing. Repeated step IDs
are counted once; cache reads and reasoning remain separate token buckets.
Missing reasoning, cache, or cache read/write fields default to zero. Input and
output counts remain required; malformed, null, negative, or non-integer counts
leave usage unknown. An error-only transcript establishes readiness and reports
the harness error without requiring an earlier success event.
GAP code cells also allow the bash, read, write, edit, glob, grep and list
tools, and run the whole Opencode tree under the bench's portable sandbox; see
[OSS harness code cells](../lib/python/sew/gap/WORKSPACE.md#ohm-08-oss-harness-code-cells).

The CLI flags were checked with `opencode --help` and `opencode run --help`
on 1.17.3. The offline replay fixture follows the pinned
[run event emitter](https://github.com/anomalyco/opencode/blob/v1.17.3/packages/opencode/src/cli/cmd/run.ts)
and [token normalization](https://github.com/anomalyco/opencode/blob/v1.17.3/packages/opencode/src/session/session.ts).
Only fake binaries replay it in tests. No live cell or provider call was used
to build this support.
