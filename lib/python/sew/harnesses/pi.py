"""Pi search cells use only the isolated LiteLLM provider and MCP bridge."""
from .registry import HarnessSpec

MIN_VERSION = "0.79.8"
FORBIDDEN_FLAGS = frozenset({
    '--provider', '--model', '--api-key', '--mode', '--tools', '-t',
    '--exclude-tools', '-xt', '--extension', '-e', '--skill', '--prompt-template',
    '--session', '--session-dir', '--continue', '-c', '--resume', '-r', '--fork',
    '--system-prompt', '--append-system-prompt', '--models', '--approve', '-a',
    '--no-tools', '-nt', '--no-builtin-tools', '-nbt', '--no-extensions', '-ne',
    '--no-skills', '-ns', '--no-prompt-templates', '-np', '--no-context-files', '-nc',
})
SPEC = HarnessSpec(
    id='pi', label='Pi', live=True, bin_env='SEW_PI_BIN', default_bin='pi',
    protocol='sew.pi_live:PiProtocol', arm_spawn='sew.pi_live:arm_spawn',
    forbidden_flags=FORBIDDEN_FLAGS, native_search=False, oss=True,
    usage_parser='sew.pi_live:usage_row', pricing_key='oss-catalog',
)
