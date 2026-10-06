from __future__ import annotations

from typing import Any, Mapping

import pytest

from sew.domain_surfaces import (
    DOMAINS,
    DOWNGRADE_ENTITLEMENT_DENIED,
    DOWNGRADE_MISSING_CONTEXT,
    DOWNGRADE_NOT_ADVERTISED,
    DOWNGRADE_UNDECLARED,
    DOWNGRADE_UNKNOWN_VARIANT,
    GENERIC_SURFACE,
    PROVIDERS,
    SURFACE_DECLARATIONS,
    DomainSurfaceError,
    assert_claim_registry_vocabulary,
    assert_surface_declarations,
    provider_id_for_claimant,
    resolve_domain_surface,
)
from sew.providers import (
    CredentialResolver,
    ProviderRequest,
    HttpProvider,
    HttpResponse,
    ProviderCapabilities,
    make_provider,
)

# The specialist arm each vendor/domain pair must select when the surface is
# both declared and reachable. Pairs absent from this map are declared-generic:
# the vendor genuinely ships no domain surface there.
# Two different vendor vocabularies, deliberately on two different context
# keys. Exa's categories are a closed set read from `exa_category`; Parallel's
# findall `entity_type` is free-form vendor text ("companies" in its own docs).
# They shared one key until a company task silently resolved to Exa's *people*
# index, so the separation is the fix rather than a convention.
EXA_COMPANY_VARIANT = "company"
PARALLEL_FINDALL_ENTITY_TYPE = "companies"


EXPECTED_SPECIALIST_SURFACES = {
    ("firecrawl", "code_and_pr"): "firecrawl.category.developer",
    ("exa", "entity_resolution"): "exa.category.people",
    ("exa", "gtm"): "exa.category.company",
    ("parallel-web", "code_and_pr"): "parallel.search.source_policy",
    ("parallel-web", "legal"): "parallel.search.source_policy",
    ("parallel-web", "entity_resolution"): "parallel.findall",
    ("parallel-web", "gtm"): "parallel.findall",
}
EXPECTED_GENERIC_PAIRS = {
    ("firecrawl", "legal"),
    ("firecrawl", "entity_resolution"),
    ("firecrawl", "gtm"),
    ("exa", "legal"),
    # Exa deprecated the `github` category (changelog 2026-07-23), and unknown
    # categories are absorbed as free-text hints rather than refused -- so a
    # declaration here could never be caught by a downgrade gate. The honest
    # arm is generic.
    ("exa", "code_and_pr"),
    # Brave and Tavily make no domain claim and ship no domain surface; they are
    # generic baselines in every domain by design, not by downgrade.
    ("brave", "code_and_pr"),
    ("brave", "legal"),
    ("brave", "entity_resolution"),
    ("brave", "gtm"),
    ("tavily", "code_and_pr"),
    ("tavily", "legal"),
    ("tavily", "entity_resolution"),
    ("tavily", "gtm"),
    # Perplexity's `search_type: people` is a candidate entity_resolution
    # surface, left undeclared until verified; generic everywhere for now.
    ("perplexity", "code_and_pr"),
    ("perplexity", "legal"),
    ("perplexity", "entity_resolution"),
    ("perplexity", "gtm"),
}


def _surface_context(provider_id: str, domain: str) -> dict[str, Any] | None:
    declaration = SURFACE_DECLARATIONS[(provider_id, domain)]
    context: dict[str, Any] = {}
    if declaration.context_option == "source_policy":
        context["source_policy"] = {"include_domains": ["github.com"]}
    if declaration.required_context:
        context.update(
            {
                "entity_type": "company",
                "match_conditions": [{"name": "ICP", "description": "Matches the task ICP"}],
            }
        )
    return context or None


class RecordingTransport:
    """Transport that replays a scripted response per call, then a default."""

    def __init__(self, responses: list[HttpResponse] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append({"url": url, "json_body": dict(json_body or {})})
        if self._responses:
            return self._responses.pop(0)
        return HttpResponse(200, {"results": []})


class RefusingTransport:
    """Refuses the plan-gated surface and serves the generic arm normally."""

    def __init__(self, *, gated_option: tuple[str, Any], generic_status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._gated_key, self._gated_value = gated_option
        self._generic_status = generic_status

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        body = dict(json_body or {})
        self.calls.append({"url": url, "json_body": body})
        if body.get(self._gated_key) == self._gated_value:
            return HttpResponse(403, {"error": "plan does not include this category"})
        return HttpResponse(
            self._generic_status, {"results": [{"url": "https://e.test", "title": "e"}]}
        )


def _providers():
    return {
        provider_id: make_provider(provider_id, live_enabled=False) for provider_id in PROVIDERS
    }


def _exa(transport, **kwargs) -> HttpProvider:
    return make_provider(
        "exa",
        credential_resolver=CredentialResolver(environ={"SEW_EXA_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
        **kwargs,
    )


def test_every_vendor_domain_pair_resolves_to_declared_surface() -> None:
    providers = _providers()

    assert set(SURFACE_DECLARATIONS) == {
        (provider_id, domain) for provider_id in PROVIDERS for domain in DOMAINS
    }
    assert set(EXPECTED_SPECIALIST_SURFACES) | EXPECTED_GENERIC_PAIRS == set(SURFACE_DECLARATIONS)
    assert_surface_declarations(
        {provider_id: provider.capabilities for provider_id, provider in providers.items()}
    )
    for provider_id, domain in SURFACE_DECLARATIONS:
        selection = resolve_domain_surface(
            provider_id,
            domain,
            providers[provider_id].capabilities,
            context=_surface_context(provider_id, domain),
        )
        assert selection.endpoint.startswith("/")
        # Asserting only `selection.surface_id` would pass even if every pair
        # degraded, because GENERIC_SURFACE is itself a non-empty string.
        if (provider_id, domain) in EXPECTED_GENERIC_PAIRS:
            assert selection.surface_id == GENERIC_SURFACE
            assert selection.generic_surface is True
            assert selection.downgrade_reason == DOWNGRADE_UNDECLARED
        else:
            assert selection.surface_id == EXPECTED_SPECIALIST_SURFACES[(provider_id, domain)]
            assert selection.generic_surface is False
            assert selection.downgrade_reason is None


def test_surface_used_is_recorded_per_provider_call() -> None:
    transport = RecordingTransport()
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search_domain(
        "fixing pull request", domain="code_and_pr", run_id="domain-cell"
    )

    assert transport.calls[0]["json_body"]["categories"] == [{"type": "developer"}]
    # The evidence record travels on ProviderRequest, never through the body.
    assert "_sew_domain_surface" not in transport.calls[0]["json_body"]
    assert "_sew_domain_surface" not in result.call_record["request"].get("option_keys", [])
    assert result.call_record["response"]["domain_surface"] == {
        "provider_id": "firecrawl",
        "domain": "code_and_pr",
        "surface_id": "firecrawl.category.developer",
        "operation": "search",
        "declared_endpoint": "/v2/search",
        # Stamped from the adapter's endpoint map, not from the declaration.
        "endpoint": "/v2/search",
        # Keys only: the surface record follows `sanitize_request`'s keys-only
        # posture rather than persisting caller-supplied option values.
        "option_keys": ["categories"],
        "generic_surface": False,
        "declared_surface_id": None,
        "downgrade_reason": None,
    }


def test_recorded_endpoint_is_the_one_actually_requested() -> None:
    transport = RecordingTransport()
    provider = HttpProvider(
        "exa",
        capabilities=ProviderCapabilities(
            provider_id="exa",
            operations=frozenset({"search"}),
            requires_credential=True,
            domain_surfaces=frozenset({"exa.category.github"}),
        ),
        base_url="https://api.example.test",
        # Declared registry endpoint for this surface is `/search`.
        endpoints={"search": "/relocated-search"},
        credential_resolver=CredentialResolver(environ={"SEW_EXA_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search_domain("bug fix PR", domain="code_and_pr", run_id="drift-cell")

    surface = result.call_record["response"]["domain_surface"]
    assert transport.calls[0]["url"].endswith("/relocated-search")
    assert surface["endpoint"] == "/relocated-search"
    assert surface["declared_endpoint"] == "/search"


def test_missing_plan_gated_surface_records_generic_instead_of_failure() -> None:
    capabilities = ProviderCapabilities(
        provider_id="exa",
        operations=frozenset({"search"}),
        requires_credential=True,
    )

    selection = resolve_domain_surface("exa", "gtm", capabilities)

    assert selection.surface_id == GENERIC_SURFACE
    assert selection.generic_surface is True
    assert selection.operation == "search"
    assert selection.downgrade_reason == DOWNGRADE_NOT_ADVERTISED
    assert selection.declared_surface_id == "exa.category.company"
    assert selection.record()["generic_surface"] is True

    transport = RecordingTransport()
    provider = HttpProvider(
        "exa",
        capabilities=capabilities,
        base_url="https://api.example.test",
        endpoints={"search": "/search"},
        credential_resolver=CredentialResolver(environ={"SEW_EXA_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )
    result = provider.search_domain("target accounts", domain="gtm", run_id="generic-cell")
    assert result.status == "ok"
    assert result.call_record["response"]["domain_surface"]["surface_id"] == GENERIC_SURFACE
    assert result.call_record["response"]["domain_surface"]["generic_surface"] is True


def test_plan_gated_entitlement_refusal_downgrades_to_generic_not_failed() -> None:
    # Exa advertises the people/company categories, but this key's plan tier
    # does not include them: the surface-specific POST is refused 403.
    transport = RefusingTransport(gated_option=("category", "company"))
    provider = _exa(transport)

    result = provider.search_domain("target accounts", domain="gtm", run_id="gated-cell")

    assert [call["json_body"].get("category") for call in transport.calls] == ["company", None]
    assert result.status == "ok"
    assert result.error_class is None
    surface = result.call_record["response"]["domain_surface"]
    assert surface["surface_id"] == GENERIC_SURFACE
    assert surface["generic_surface"] is True
    assert surface["downgrade_reason"] == DOWNGRADE_ENTITLEMENT_DENIED
    assert surface["declared_surface_id"] == "exa.category.company"

    # The refused attempt is the only evidence for the downgrade this record
    # asserts, so it is carried forward rather than dropped with `result`.
    evidence = surface["downgrade_evidence"]
    assert evidence["error_class"] == "http_403"
    assert evidence["http_status"] == 403
    assert evidence["status"] == "refused"
    assert evidence["call_id"]
    # Two billed round trips happened; retry accounting must show the second.
    assert result.call_record["retry_count"] == 1

    # One refusal is an observation, not an entitlement fact: a single WAF 403
    # must not rewrite every later cell for this surface.
    assert surface["refusal_observations"] == 1
    assert surface["entitlement_latched"] is False
    assert provider.unavailable_domain_surfaces == frozenset()

    # A second corroborating refusal latches it, and only then does the next
    # cell skip the probe.
    transport.calls.clear()
    second = provider.search_domain("more accounts", domain="gtm", run_id="gated-cell-2")
    second_surface = second.call_record["response"]["domain_surface"]
    assert second_surface["refusal_observations"] == 2
    assert second_surface["entitlement_latched"] is True
    assert provider.unavailable_domain_surfaces == frozenset({"exa.category.company"})

    transport.calls.clear()
    again = provider.search_domain("even more accounts", domain="gtm", run_id="gated-cell-3")
    assert len(transport.calls) == 1
    assert "category" not in transport.calls[0]["json_body"]
    assert again.call_record["response"]["domain_surface"]["generic_surface"] is True


def test_account_wide_refusal_stays_a_provider_failure() -> None:
    # A bad or suspended credential refuses the generic arm too. That is an
    # honest provider failure and must not be laundered into generic_surface.
    transport = RefusingTransport(gated_option=("category", "company"), generic_status=403)
    provider = _exa(transport)

    result = provider.search_domain("target accounts", domain="gtm", run_id="dead-key-cell")

    assert result.status == "refused"
    assert result.error_class == "http_403"
    assert result.call_record["response"]["domain_surface"]["surface_id"] == "exa.category.company"
    assert provider.unavailable_domain_surfaces == frozenset()


def test_operator_can_preseed_a_known_entitlement_gap() -> None:
    transport = RecordingTransport()
    provider = _exa(transport)
    provider.mark_domain_surface_unavailable("exa.category.people")

    selection = resolve_domain_surface(
        "exa",
        "entity_resolution",
        provider.capabilities,
        unavailable_surfaces=provider.unavailable_domain_surfaces,
    )

    assert selection.surface_id == GENERIC_SURFACE
    assert selection.downgrade_reason == DOWNGRADE_ENTITLEMENT_DENIED

    result = provider.search_domain("who is Jane Doe", domain="entity_resolution", run_id="pre")
    assert len(transport.calls) == 1
    assert "category" not in transport.calls[0]["json_body"]
    assert result.call_record["response"]["domain_surface"]["generic_surface"] is True


def test_surface_request_shapes_match_provider_adapters() -> None:
    providers = _providers()

    exa_people = resolve_domain_surface("exa", "entity_resolution", providers["exa"].capabilities)
    exa_company = resolve_domain_surface(
        "exa",
        "entity_resolution",
        providers["exa"].capabilities,
        context={"exa_category": "company"},
    )
    parallel_legal = resolve_domain_surface(
        "parallel-web",
        "legal",
        providers["parallel-web"].capabilities,
        context={"source_policy": {"include_domains": ["supremecourt.gov"]}},
    )
    parallel_gtm = resolve_domain_surface(
        "parallel-web",
        "gtm",
        providers["parallel-web"].capabilities,
        context=_surface_context("parallel-web", "gtm"),
    )

    assert exa_people.options == {"category": "people"}
    assert exa_company.surface_id == "exa.category.company"
    assert exa_company.options == {"category": "company"}
    assert parallel_legal.options == {
        "advanced_settings": {"source_policy": {"include_domains": ["supremecourt.gov"]}}
    }
    assert parallel_gtm.operation == "findall"
    assert parallel_gtm.endpoint == "/v1beta/findall/runs"


def test_missing_required_context_degrades_instead_of_calling_malformed() -> None:
    providers = _providers()

    # source_policy arm: no policy supplied.
    legal = resolve_domain_surface("parallel-web", "legal", providers["parallel-web"].capabilities)
    assert legal.surface_id == GENERIC_SURFACE
    assert legal.downgrade_reason == DOWNGRADE_MISSING_CONTEXT

    # findall arm: entity_type alone is not a submittable run.
    partial = resolve_domain_surface(
        "parallel-web",
        "gtm",
        providers["parallel-web"].capabilities,
        context={"entity_type": "company"},
    )
    assert partial.surface_id == GENERIC_SURFACE
    assert partial.generic_surface is True
    assert partial.downgrade_reason == DOWNGRADE_MISSING_CONTEXT
    assert partial.operation == "search"


def test_parallel_findall_surface_uses_entity_endpoint_and_records_cell() -> None:
    transport = RecordingTransport(
        [HttpResponse(200, {"findall_id": "fa_123", "status": "queued"})]
    )
    provider = make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search_domain(
        "Find companies matching the ICP",
        domain="gtm",
        run_id="findall-cell",
        surface_context={
            "entity_type": "company",
            "match_conditions": [{"name": "ICP", "description": "Matches the task ICP"}],
            "match_limit": 5,
            "generator": "base",
        },
    )

    assert transport.calls[0]["url"].endswith("/v1beta/findall/runs")
    assert transport.calls[0]["json_body"] == {
        "objective": "Find companies matching the ICP",
        "entity_type": "company",
        "match_conditions": [{"name": "ICP", "description": "Matches the task ICP"}],
        "match_limit": 5,
        "generator": "base",
    }
    assert result.operation == "findall"
    assert result.call_record["response"]["domain_surface"]["surface_id"] == "parallel.findall"


def test_findall_submission_is_not_scored_as_zero_result_retrieval() -> None:
    transport = RecordingTransport([HttpResponse(200, {"run_id": "fa_987", "status": "queued"})])
    provider = make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search_domain(
        "Find companies matching the ICP",
        domain="gtm",
        run_id="pending-cell",
        surface_context={
            "entity_type": "company",
            "match_conditions": [{"name": "ICP", "description": "Matches the task ICP"}],
        },
    )

    assert result.status == "not_applicable"
    # The scripted acknowledgement reports `status: "queued"` as a bare
    # string, which is not a readable run state, so the poll settles at once
    # and fails the completion check instead of fetching results that do not
    # exist yet. Distinct from a genuinely still-running job.
    assert result.error_class == "async_run_not_completed"
    assert result.sources == ()
    response = result.call_record["response"]
    assert response["async_submission"] is True
    # The handle is persisted even though the vendor used `run_id`, not the
    # single key the first cut guessed.
    assert response["async_run_handle"] == "fa_987"
    assert response["provider_units"]["async_run_handle"] == "fa_987"
    assert "result_count" not in response


def test_findall_acknowledgement_without_run_handle_fails_loudly() -> None:
    transport = RecordingTransport([HttpResponse(200, {"status": "queued"})])
    provider = make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search_domain(
        "Find companies matching the ICP",
        domain="entity_resolution",
        run_id="handleless-cell",
        surface_context={
            "entity_type": "company",
            "match_conditions": [{"name": "ICP", "description": "Matches the task ICP"}],
        },
    )

    assert result.status == "failed"
    assert result.error_class == "async_run_handle_missing"
    assert result.call_record["response"]["async_run_handle"] is None


def test_undeclared_domain_returns_result_instead_of_raising() -> None:
    provider = make_provider("exa", live_enabled=False)

    result = provider.search_domain("anything", domain="astrology", run_id="bad-domain")

    assert result.status == "not_applicable"
    assert result.error_class == "undeclared_domain_surface"
    # The registry itself still refuses an undeclared pair loudly.
    with pytest.raises(DomainSurfaceError, match="undeclared domain surface"):
        resolve_domain_surface("exa", "astrology", provider.capabilities)


def test_capability_metadata_cannot_advertise_undeclared_surface() -> None:
    capabilities = {
        provider_id: provider.capabilities for provider_id, provider in _providers().items()
    }
    capabilities["exa"] = ProviderCapabilities(
        provider_id="exa",
        operations=frozenset({"search"}),
        requires_credential=True,
        domain_surfaces=frozenset({"exa.category.unknown"}),
    )

    with pytest.raises(DomainSurfaceError, match="undeclared capability metadata"):
        assert_surface_declarations(capabilities)


def test_inconclusive_generic_retry_does_not_poison_the_run() -> None:
    # The gated surface is refused, but the generic probe times out rather than
    # succeeding. That is no evidence of an entitlement gap, so the original
    # refusal stands and no surface is marked unavailable for later cells.
    transport = RecordingTransport(
        [HttpResponse(403, {"error": "plan gate"}), HttpResponse(504, {"error": "gateway"})]
    )
    provider = _exa(transport)

    result = provider.search_domain("target accounts", domain="gtm", run_id="flaky-cell")

    assert len(transport.calls) == 2
    assert result.status == "refused"
    assert result.call_record["response"]["domain_surface"]["surface_id"] == "exa.category.company"
    assert provider.unavailable_domain_surfaces == frozenset()


class FindAllTransport:
    """Submit-then-poll FindAll: POST receipt, N active polls, then results."""

    def __init__(self, *, active_polls: int = 1, final_status: str = "completed") -> None:
        self.calls: list[dict[str, Any]] = []
        self._active_polls = active_polls
        self._final_status = final_status

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append({"method": method, "url": url, "json_body": dict(json_body or {})})
        if method == "POST":
            return HttpResponse(200, {"findall_id": "fa_poll"})
        if url.endswith("/result"):
            return HttpResponse(
                200,
                {
                    "candidates": [
                        {
                            "name": "Matched Co",
                            "url": "https://matched.test",
                            "match_status": "matched",
                        },
                        # FindAll also returns entities it considered and
                        # rejected; counting those would inflate recall with
                        # rows the vendor itself refused.
                        {
                            "name": "Rejected Co",
                            "url": "https://rejected.test",
                            "match_status": "rejected",
                        },
                    ]
                },
            )
        if self._active_polls > 0:
            self._active_polls -= 1
            return HttpResponse(200, {"status": {"is_active": True, "status": "running"}})
        return HttpResponse(200, {"status": {"is_active": False, "status": self._final_status}})


def _parallel(transport, **kwargs) -> HttpProvider:
    kwargs.setdefault("live_enabled", True)
    return make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
        transport=transport,
        **kwargs,
    )


def _findall_context() -> dict[str, Any]:
    return {
        "entity_type": PARALLEL_FINDALL_ENTITY_TYPE,
        "match_conditions": [{"name": "is_saas", "description": "Company sells SaaS."}],
    }


def test_http_451_is_not_entitlement_evidence() -> None:
    # 451 is a legal block on the *content*, not a statement about what this
    # plan bought. Latching on it would move a legal-domain cell off its
    # specialist arm for the wrong reason, and the report would then assert
    # entitlement denial as fact.
    transport = RefusingTransport(gated_option=("category", "company"))

    def _blocked(method, url, *, headers, json_body, timeout_seconds):
        transport.calls.append({"url": url, "json_body": dict(json_body or {})})
        return HttpResponse(451, {"error": "unavailable for legal reasons"})

    transport.request = _blocked  # type: ignore[method-assign]
    provider = _exa(transport)

    result = provider.search_domain("target accounts", domain="gtm", run_id="blocked-cell")

    # No probe, no downgrade, no memo: the refusal stands as the provider result.
    assert len(transport.calls) == 1
    assert result.error_class == "http_451"
    assert provider.unavailable_domain_surfaces == frozenset()
    assert provider.domain_surface_refusals == {}
    surface = result.call_record["response"]["domain_surface"]
    assert surface["generic_surface"] is False
    assert surface["surface_id"] == "exa.category.company"


def test_findall_run_is_polled_to_completion_and_yields_matched_sources() -> None:
    transport = FindAllTransport(active_polls=2)
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0

    result = provider.search_domain(
        "SaaS companies in the EU",
        domain="gtm",
        run_id="findall-poll",
        surface_context=_findall_context(),
    )

    methods = [call["method"] for call in transport.calls]
    assert methods[0] == "POST"
    assert methods.count("GET") == 4  # two active polls, one settled, one result
    assert transport.calls[-1]["url"].endswith("/v1beta/findall/runs/fa_poll/result")

    # The cell is a real retrieval now, not an acknowledgement.
    assert result.status == "ok"
    assert result.error_class is None
    response = result.call_record["response"]
    assert response["async_run_resolved"] is True
    assert response["async_run_handle"] == "fa_poll"
    assert response["result_count"] == 1
    assert len(result.sources) == 1
    assert result.sources[0]["url"] == "https://matched.test"


def test_findall_run_that_never_settles_returns_the_pending_receipt() -> None:
    # Bounded: an unfinished run must degrade to the acknowledgement rather
    # than block a cell or invent an empty result set.
    transport = FindAllTransport(active_polls=99)
    provider = _parallel(transport)
    provider.ASYNC_POLL_MAX_ATTEMPTS = 3
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0

    result = provider.search_domain(
        "SaaS companies in the EU",
        domain="gtm",
        run_id="findall-stuck",
        surface_context=_findall_context(),
    )

    assert result.status == "not_applicable"
    assert result.error_class == "async_run_pending"
    assert result.sources == ()


def test_findall_run_that_fails_does_not_fetch_results() -> None:
    transport = FindAllTransport(active_polls=0, final_status="cancelled")
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0

    result = provider.search_domain(
        "SaaS companies in the EU",
        domain="gtm",
        run_id="findall-cancelled",
        surface_context=_findall_context(),
    )

    assert not any(call["url"].endswith("/result") for call in transport.calls)
    # A settled-but-not-completed run is a different fact from a pending one;
    # collapsing them makes a vendor status-string change indistinguishable
    # from normal operation.
    assert result.error_class == "async_run_not_completed"
    assert result.call_record["error_class"] == "async_run_not_completed"
    observed = result.call_record["response"]["async_observed_status"]
    assert observed["status"] == "cancelled"


def test_parallel_source_policy_nests_under_advanced_settings_with_sizing() -> None:
    # The interaction that can actually break is the merge order in
    # `_request_body`: `advanced_settings` is popped, then max_results and
    # excerpt_settings are layered in. Nothing exercised that with a
    # surface-supplied source_policy until now. Nesting verified against
    # docs.parallel.ai 2026-09-21.
    transport = RecordingTransport()
    provider = _parallel(transport)

    provider.search_domain(
        "EU merger filings",
        domain="legal",
        run_id="legal-cell",
        surface_context={"source_policy": {"include_domains": ["curia.europa.eu"]}},
        num_results=5,
        max_chars_per_result=4000,
    )

    body = transport.calls[0]["json_body"]
    assert "source_policy" not in body, "a stray top-level source_policy is not the wire shape"
    advanced = body["advanced_settings"]
    assert advanced["source_policy"] == {"include_domains": ["curia.europa.eu"]}
    assert advanced["max_results"] == 5
    assert "excerpt_settings" in advanced


def test_endpoint_is_absent_when_no_request_was_issued() -> None:
    # "Surface used" must report the observation. A cell that never issued a
    # request must not name an endpoint as the one it used.
    transport = RecordingTransport()
    provider = _parallel(transport, live_enabled=False)

    result = provider.search_domain(
        "EU merger filings",
        domain="legal",
        run_id="offline-cell",
        surface_context={"source_policy": {"include_domains": ["curia.europa.eu"]}},
    )

    assert transport.calls == []
    assert result.error_class == "live_smoke_disabled"
    surface = result.call_record["response"]["domain_surface"]
    assert "endpoint" not in surface
    assert surface["declared_endpoint"] == "/v1/search"


def test_claim_registry_vocabulary_joins_onto_providers_and_domains() -> None:
    # DSB-07/08 must join claim -> provider -> surface. The registry says
    # `parallel`; the adapters say `parallel-web`. They only join through the
    # declared mapping, so assert it rather than leaving it to coincide.
    import pathlib

    import yaml

    registry = pathlib.Path(__file__).resolve().parents[1] / "catalogs/domains/claims.yaml"
    claims = yaml.safe_load(registry.read_text())["claims"]
    assert claims, "claim registry must not be empty"

    assert_claim_registry_vocabulary(claims)
    assert {provider_id_for_claimant(c["claimant"]) for c in claims} <= PROVIDERS
    assert provider_id_for_claimant("parallel") == "parallel-web"


def test_claim_registry_vocabulary_rejects_an_unjoinable_claimant() -> None:
    with pytest.raises(DomainSurfaceError, match="unknown claim registry claimant"):
        assert_claim_registry_vocabulary(
            [{"hypothesis_id": "HX", "claimant": "parallel-web-systems", "domain": "legal"}]
        )


def test_claim_registry_vocabulary_rejects_an_undeclared_domain() -> None:
    with pytest.raises(DomainSurfaceError, match="undeclared domain"):
        assert_claim_registry_vocabulary(
            [{"hypothesis_id": "HX", "claimant": "exa", "domain": "biomedical"}]
        )


def test_unknown_variant_value_records_a_downgrade_not_the_base_surface() -> None:
    # A *present but unrecognised* entity_type must not silently select the
    # base `people` arm: H3 would then read a company task as having exercised
    # Exa's people index, with `generic_surface: false` asserting it as fact.
    providers = _providers()
    selection = resolve_domain_surface(
        "exa",
        "entity_resolution",
        providers["exa"].capabilities,
        context={"exa_category": "organisation"},  # not a declared Exa category
    )

    assert selection.surface_id == GENERIC_SURFACE
    assert selection.generic_surface is True
    assert selection.downgrade_reason == DOWNGRADE_UNKNOWN_VARIANT
    assert selection.declared_surface_id == "exa.category.people"


def test_absent_variant_key_still_selects_the_declared_base_surface() -> None:
    # An absent key is the declared default, not a failed lookup.
    providers = _providers()
    selection = resolve_domain_surface(
        "exa", "entity_resolution", providers["exa"].capabilities, context={}
    )
    assert selection.surface_id == "exa.category.people"
    assert selection.generic_surface is False
    assert selection.downgrade_reason is None


def test_declared_variant_value_still_selects_the_variant() -> None:
    providers = _providers()
    selection = resolve_domain_surface(
        "exa",
        "entity_resolution",
        providers["exa"].capabilities,
        context={"exa_category": EXA_COMPANY_VARIANT},
    )
    assert selection.surface_id == "exa.category.company"
    assert selection.generic_surface is False


def test_async_poll_is_bounded_by_the_callers_wall_clock_budget() -> None:
    # Attempt-count bounding alone silently multiplies the operator's budget:
    # 30 attempts x 5s sleeps x a per-GET timeout is ~33 minutes inside one
    # cell, and nothing between cells interrupts it.
    transport = FindAllTransport(active_polls=99)
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 5.0

    clock = {"t": 0.0}

    def _monotonic() -> float:
        return clock["t"]

    def _sleep(seconds: float) -> None:
        clock["t"] += seconds

    # The poll horizon is its own budget, independent of the per-request
    # timeout: reusing `timeout_seconds` made ASYNC_POLL_MAX_ATTEMPTS
    # unreachable at every suite timeout the repo ships.
    provider.ASYNC_POLL_BUDGET_SECONDS = 12.0

    result = provider.resolve_async_run(
        provider.search_domain(
            "SaaS companies",
            domain="gtm",
            run_id="budget",
            surface_context=_findall_context(),
        ),
        request=ProviderRequest("findall", run_id="budget", query="x", timeout_seconds=60.0),
        sleep=_sleep,
        monotonic=_monotonic,
    )

    # Stops on the poll budget, well short of the 30-attempt cap, and does not
    # inherit the much larger per-request timeout.
    assert clock["t"] <= 12.0
    assert result.error_class == "async_poll_deadline_exceeded"


def test_async_poll_budget_can_actually_reach_the_attempt_cap() -> None:
    # The two bounds must not contradict each other: whichever is smaller
    # silently becomes the real limit, and a dead attempt cap hides that.
    provider = _parallel(FindAllTransport())
    needed = provider.ASYNC_POLL_MAX_ATTEMPTS * provider.ASYNC_POLL_INTERVAL_SECONDS
    assert provider.ASYNC_POLL_BUDGET_SECONDS >= needed


def test_async_poll_disabled_returns_the_receipt_rather_than_crashing() -> None:
    # `ASYNC_POLL_MAX_ATTEMPTS = 0` is the obvious way to turn polling off; it
    # must not raise UnboundLocalError on the post-loop status check.
    transport = FindAllTransport(active_polls=0)
    provider = _parallel(transport)
    provider.ASYNC_POLL_MAX_ATTEMPTS = 0

    result = provider.search_domain(
        "SaaS companies", domain="gtm", run_id="off", surface_context=_findall_context()
    )
    assert result.error_class == "async_poll_disabled"
    assert result.sources == ()


def test_async_run_handle_is_rejected_when_it_could_escape_the_url_path() -> None:
    class EvilHandleTransport(FindAllTransport):
        def request(self, method, url, *, headers, json_body, timeout_seconds):
            self.calls.append({"method": method, "url": url, "json_body": dict(json_body or {})})
            if method == "POST":
                return HttpResponse(200, {"findall_id": "../../v1/other?x=1"})
            return HttpResponse(200, {"status": {"is_active": False, "status": "completed"}})

    transport = EvilHandleTransport()
    provider = _parallel(transport)

    result = provider.search_domain(
        "SaaS companies", domain="gtm", run_id="evil", surface_context=_findall_context()
    )

    # Only the POST happened: no authenticated GET was built from that handle.
    assert [call["method"] for call in transport.calls] == ["POST"]
    assert result.error_class == "async_run_handle_malformed"


def test_resolved_async_run_keeps_the_submission_as_its_own_call_record() -> None:
    # `max_provider_calls` is enforced against persisted provider_call records,
    # so a cell that issued several round trips while persisting one makes the
    # operator's budget off by that factor.
    transport = FindAllTransport(active_polls=1)
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0

    result = provider.search_domain(
        "SaaS companies", domain="gtm", run_id="acct", surface_context=_findall_context()
    )

    assert result.status == "ok"
    assert len(result.extra_call_records) == 1
    assert result.extra_call_records[0]["response"]["async_submission"] is True
    assert result.call_record["response"]["async_poll_calls"] == 3


def test_entitlement_downgrade_keeps_the_refused_attempt_as_a_call_record() -> None:
    transport = RefusingTransport(gated_option=("category", "company"))
    provider = _exa(transport)

    result = provider.search_domain("target accounts", domain="gtm", run_id="billed")

    assert len(result.extra_call_records) == 1
    refused = result.extra_call_records[0]
    assert refused["error_class"] == "http_403"
    assert result.retry_count == 1
    assert result.call_record["retry_count"] == 1


def test_catalog_surface_context_lets_parallel_reach_its_specialist_arms() -> None:
    """The declared seam a domain task uses to carry surface context.

    Without it, Parallel's four declarations can never receive their context,
    so Parallel resolves generic in all four domains while Firecrawl and Exa
    get specialist arms -- the asymmetry DSB exists to prevent.
    """
    from sew.catalog import SURFACE_CONTEXT_KEYS

    providers = _providers()
    capabilities = providers["parallel-web"].capabilities

    legal_context = {"source_policy": {"include_domains": ["curia.europa.eu"]}}
    gtm_context = {
        "entity_type": PARALLEL_FINDALL_ENTITY_TYPE,
        "match_conditions": [{"name": "is_saas", "description": "Company sells SaaS."}],
    }
    # Every key these arms need is accepted by the task schema.
    assert set(legal_context) <= SURFACE_CONTEXT_KEYS
    assert set(gtm_context) <= SURFACE_CONTEXT_KEYS

    for domain, context, expected in (
        ("legal", legal_context, "parallel.search.source_policy"),
        ("code_and_pr", legal_context, "parallel.search.source_policy"),
        ("entity_resolution", gtm_context, "parallel.findall"),
        ("gtm", gtm_context, "parallel.findall"),
    ):
        selection = resolve_domain_surface("parallel-web", domain, capabilities, context=context)
        assert selection.surface_id == expected, domain
        assert selection.generic_surface is False, domain


def test_domain_task_schema_accepts_and_validates_surface_context() -> None:
    from sew.catalog import _DOMAIN_TASK_KEYS, SURFACE_CONTEXT_KEYS

    assert "surface_context" in _DOMAIN_TASK_KEYS
    # Vendor-neutral keys only; an adapter-specific knob must not leak in.
    assert "advanced_settings" not in SURFACE_CONTEXT_KEYS
    assert {"source_policy", "entity_type", "match_conditions"} <= SURFACE_CONTEXT_KEYS


def test_every_claimant_reaches_its_specialist_arm_from_the_catalog_alone() -> None:
    """The property the hand-built-context test could not assert.

    A vendor that resolves generic while its competitors resolve specialist has
    already lost the comparison before a query runs, and the `generic_surface`
    marking makes that look honest. This resolves the REAL catalog, so a task
    file that cannot satisfy a declaration fails here rather than in a report.
    """
    import pathlib

    import yaml

    from sew.domain_surfaces import CLAIMANT_PROVIDER_IDS

    root = pathlib.Path(__file__).resolve().parents[1] / "catalogs/domains"
    tasks = yaml.safe_load((root / "tasks.yaml").read_text())["tasks"]
    claims = yaml.safe_load((root / "claims.yaml").read_text())["claims"]
    claimant_by_domain = {c["domain"]: c["claimant"] for c in claims}
    providers = _providers()

    for task in tasks:
        domain = task["domain"]
        claimant = CLAIMANT_PROVIDER_IDS[claimant_by_domain[domain]]
        selection = resolve_domain_surface(
            claimant,
            domain,
            providers[claimant].capabilities,
            context=task.get("surface_context") or {},
        )
        assert selection.generic_surface is False, (
            f"{claimant} runs generic in its own claimed domain {domain} "
            f"for task {task['id']}: {selection.downgrade_reason}"
        )


def test_declared_surfaces_engage_from_the_catalog_for_every_vendor() -> None:
    # Not just the claimant: any vendor with a declared surface for a domain
    # must be able to reach it from the catalog, or the table is skewed.
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[1] / "catalogs/domains"
    tasks = yaml.safe_load((root / "tasks.yaml").read_text())["tasks"]
    providers = _providers()

    for task in tasks:
        context = task.get("surface_context") or {}
        for (provider_id, domain), declaration in SURFACE_DECLARATIONS.items():
            if domain != task["domain"] or declaration.surface_id is None:
                continue
            selection = resolve_domain_surface(
                provider_id, domain, providers[provider_id].capabilities, context=context
            )
            assert selection.generic_surface is False, (
                f"{provider_id} declares {declaration.surface_id} for {domain} but task "
                f"{task['id']} cannot engage it: {selection.downgrade_reason}"
            )


def test_dot_only_async_handles_are_rejected() -> None:
    # `.` and `..` are unreserved, so a charset check alone accepts them and
    # percent-encoding leaves them intact -- yielding `/runs/..` for the
    # vendor's router to normalise into a different endpoint.
    from sew.async_poll import _ASYNC_RUN_HANDLE_RE

    for bad in ("..", ".", "...", "._-~"):
        assert not _ASYNC_RUN_HANDLE_RE.fullmatch(bad), bad
    for good in ("findall_40e0ab8c", "fa.1", "a"):
        assert _ASYNC_RUN_HANDLE_RE.fullmatch(good), good


class FlakyFindAllTransport(FindAllTransport):
    """FindAll whose status and result GETs fail a scripted number of times.

    Each entry in ``status_failures`` / ``result_failures`` is either an
    ``HttpResponse`` to return or an exception to raise, consumed in order
    before the healthy behavior resumes.
    """

    def __init__(
        self,
        *,
        active_polls: int = 0,
        status_failures: list[Any] | None = None,
        result_failures: list[Any] | None = None,
    ) -> None:
        super().__init__(active_polls=active_polls)
        self._status_failures = list(status_failures or [])
        self._result_failures = list(result_failures or [])

    def request(self, method, url, *, headers, json_body, timeout_seconds):
        if method == "GET":
            queue = self._result_failures if url.endswith("/result") else self._status_failures
            if queue:
                failure = queue.pop(0)
                self.calls.append({"method": method, "url": url, "json_body": {}})
                if isinstance(failure, BaseException):
                    raise failure
                return failure
        return super().request(
            method, url, headers=headers, json_body=json_body, timeout_seconds=timeout_seconds
        )


def _resolve_findall(provider: HttpProvider, run_id: str) -> Any:
    """Drive a FindAll cell with sleeps collapsed so tests stay fast."""
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0
    provider.ASYNC_RETRY_BACKOFF_SECONDS = 0
    return provider.search_domain(
        "SaaS companies", domain="gtm", run_id=run_id, surface_context=_findall_context()
    )


def test_transient_status_poll_failure_does_not_discard_a_healthy_run() -> None:
    # A 502 on ONE status GET is the vendor's edge hiccupping, not the run
    # failing. Aborting here threw away every minute already spent polling a
    # job that went on to complete normally.
    transport = FlakyFindAllTransport(
        active_polls=1,
        status_failures=[HttpResponse(502, {"error": "bad gateway"}), TimeoutError("read timeout")],
    )
    provider = _parallel(transport)

    result = _resolve_findall(provider, "flaky-status")

    assert result.status == "ok"
    assert result.error_class is None
    assert [source["url"] for source in result.sources] == ["https://matched.test"]
    # Both failed GETs are still round trips the operator paid for, so the
    # poll-call count must include them.
    assert result.call_record["response"]["async_poll_calls"] == 5


def test_non_retryable_status_poll_failure_stops_immediately_and_names_the_status() -> None:
    # A 404 means the run is gone; asking 29 more times only burns the poll
    # budget. The recorded class has to carry the code an operator would act on.
    transport = FlakyFindAllTransport(status_failures=[HttpResponse(404, {"error": "no such run"})])
    provider = _parallel(transport)

    result = _resolve_findall(provider, "lost-run")

    assert result.error_class == "async_poll_http_404"
    assert result.call_record["error_class"] == "async_poll_http_404"
    # POST + exactly one status GET: no retry storm against a dead handle.
    assert [call["method"] for call in transport.calls] == ["POST", "GET"]


def test_exhausted_status_retries_report_the_transport_failure_not_a_pending_run() -> None:
    # Every attempt failed transiently. Reporting `async_run_pending` would
    # claim a healthy still-running job we never actually observed.
    transport = FlakyFindAllTransport(
        status_failures=[HttpResponse(503, {"error": "unavailable"})] * 4
    )
    provider = _parallel(transport)
    provider.ASYNC_POLL_MAX_ATTEMPTS = 3

    result = _resolve_findall(provider, "all-transient")

    assert result.error_class == "async_poll_http_503"
    assert result.call_record["response"]["async_poll_calls"] == 3


def test_final_result_fetch_retries_a_transient_failure() -> None:
    # The result fetch is a single large-payload download at the end of a paid
    # run; one unretried timeout discarded the whole thing.
    transport = FlakyFindAllTransport(
        result_failures=[TimeoutError("read timeout"), HttpResponse(502, {"error": "bad gateway"})]
    )
    provider = _parallel(transport)

    result = _resolve_findall(provider, "flaky-result")

    assert result.status == "ok"
    assert [source["url"] for source in result.sources] == ["https://matched.test"]
    assert result.call_record["response"]["async_poll_calls"] == 4


def test_result_fetch_backs_off_between_attempts_and_stays_inside_the_budget() -> None:
    # Backoff must grow, and must never push the cell past the operator's
    # wall-clock budget: the deadline wins over the retry schedule.
    transport = FlakyFindAllTransport(result_failures=[HttpResponse(502, {})] * 10)
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0
    provider.ASYNC_RETRY_BACKOFF_SECONDS = 1.0
    provider.ASYNC_POLL_BUDGET_SECONDS = 60.0

    clock = {"t": 0.0}
    slept: list[float] = []

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    result = provider.resolve_async_run(
        provider.search_domain(
            "SaaS companies", domain="gtm", run_id="backoff", surface_context=_findall_context()
        ),
        request=ProviderRequest("findall", run_id="backoff", query="x", timeout_seconds=60.0),
        sleep=_sleep,
        monotonic=lambda: clock["t"],
    )

    assert slept == [1.0, 2.0, 4.0]
    assert clock["t"] <= 60.0
    assert result.error_class == "async_result_http_502"


def test_exhausted_result_retries_name_the_real_status_code() -> None:
    # `async_poll_unreachable` collapsed a 429, a 502 and a socket timeout into
    # one string, so an operator could not tell backoff from an outage.
    transport = FlakyFindAllTransport(
        result_failures=[HttpResponse(429, {"error": "slow down"})] * 8
    )
    provider = _parallel(transport)

    result = _resolve_findall(provider, "rate-limited")

    assert result.error_class == "async_result_http_429"
    assert result.call_record["error_class"] == "async_result_http_429"
    # Bounded: the submission POST, one status GET, then exactly
    # ASYNC_RESULT_MAX_ATTEMPTS result GETs.
    assert (
        result.call_record["response"]["async_poll_calls"] == 1 + provider.ASYNC_RESULT_MAX_ATTEMPTS
    )


def test_deadline_during_result_retries_records_what_was_being_retried() -> None:
    # "We ran out of time" must not hide "we ran out of time being rate
    # limited" -- those are different operator actions.
    transport = FlakyFindAllTransport(result_failures=[HttpResponse(429, {})] * 8)
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0
    provider.ASYNC_RETRY_BACKOFF_SECONDS = 30.0
    provider.ASYNC_POLL_BUDGET_SECONDS = 10.0

    clock = {"t": 0.0}

    def _sleep(seconds: float) -> None:
        clock["t"] += seconds

    result = provider.resolve_async_run(
        provider.search_domain(
            "SaaS companies", domain="gtm", run_id="cut", surface_context=_findall_context()
        ),
        request=ProviderRequest("findall", run_id="cut", query="x", timeout_seconds=60.0),
        sleep=_sleep,
        monotonic=lambda: clock["t"],
    )

    assert result.error_class == "async_poll_deadline_exceeded"
    assert result.call_record["response"]["async_poll_last_error"] == "http_429"


def test_poll_failure_does_not_mutate_the_receipt_it_was_handed() -> None:
    # `ProviderResult` is frozen and its call record is shared with whatever
    # built it; editing either in place makes the immutability cosmetic and
    # would corrupt a cached or replayed instance.
    transport = FlakyFindAllTransport(status_failures=[HttpResponse(403, {"error": "denied"})])
    provider = _parallel(transport)
    provider.ASYNC_POLL_INTERVAL_SECONDS = 0

    submission = provider.call(
        ProviderRequest(
            "findall",
            run_id="frozen",
            query="SaaS companies",
            options=dict(_findall_context()),
            timeout_seconds=30.0,
        )
    )
    assert submission.error_class == "async_run_pending"
    record_before = dict(submission.call_record)

    failed = provider.resolve_async_run(
        submission,
        request=ProviderRequest("findall", run_id="frozen", query="x", timeout_seconds=30.0),
    )

    assert failed is not submission
    assert failed.error_class == "async_poll_http_403"
    assert submission.error_class == "async_run_pending"
    assert submission.call_record == record_before


def test_http_retryable_separates_transient_status_codes_from_terminal_ones() -> None:
    retryable = HttpProvider._http_retryable
    assert [retryable(code) for code in (408, 429, 500, 502, 503, 504)] == [True] * 6
    assert [retryable(code) for code in (400, 401, 403, 404, 422, 451)] == [False] * 6
