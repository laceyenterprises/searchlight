"""Behavioral parity while the workbench is still in the source repository."""

import importlib.util
from pathlib import Path
import pytest
from sew import backoff, provider_errors, meter_pricing

SOURCE = Path(__file__).resolve().parents[3]


def original(path):
    if not (SOURCE / path).is_file():
        pytest.skip("Agent OS source absent after extraction")
    spec = importlib.util.spec_from_file_location("original_helper", SOURCE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("attempt", [-5, 0, 1, 8, 1023, 1024, 10000])
@pytest.mark.parametrize("base,maximum", [(0, 1), (1, 0), (-1, 4), (0.5, 2), (5, 2)])
@pytest.mark.parametrize("jitter", ["none", "full", "invalid"])
def test_backoff_parity(attempt, base, maximum, jitter):
    source = original("platform/agent-os-config/src/agent_os_config/backoff.py")
    kwargs = dict(
        base_seconds=base, max_seconds=maximum, jitter=jitter, rng=lambda low, high: high / 2
    )
    try:
        expected = source.bounded_exponential_delay(attempt, **kwargs)
    except ValueError:
        with pytest.raises(ValueError):
            backoff.bounded_exponential_delay(attempt, **kwargs)
    else:
        assert backoff.bounded_exponential_delay(attempt, **kwargs) == expected


def test_entire_scrub_vocabulary_parity():
    source = original("platform/agent-os-config/src/agent_os_core/provider_errors.py")
    assert provider_errors.FALLBACK_SCRUB_CONTRACT == source.FALLBACK_SCRUB_CONTRACT
    samples = ["ordinary error", "Bearer " + "a" * 30]
    samples += [prefix + "a" * 30 for prefix in source.FALLBACK_SCRUB_CONTRACT["token_prefixes"]]
    samples += [header + " sensitive-value" for header in source.FALLBACK_SCRUB_CONTRACT["headers"]]
    for key in source.FALLBACK_SCRUB_CONTRACT["body_keys"]:
        samples += ['{"' + key + '":"sensitive-value"}', "?" + key + "=sensitive-value&safe=1"]
    for sample in samples:
        assert provider_errors.scrub_provider_error_text(
            sample
        ) == source.scrub_provider_error_text(sample)


def test_pricing_source_parity():
    source = original("modules/worker-pool/lib/python/cwp_dispatch/mcp_metering/pricing.py")
    # Existing SEW metering tests exercise the portable pricing implementation.
    assert Path(meter_pricing.__file__).read_text() == Path(source.__file__).read_text()
