"""Declared, auditable provider surfaces for Domain Strength Bakeoff cells.

Every surface choice lives in ``SURFACE_DECLARATIONS`` -- including the
conditional variants and the honest generic arms -- so no vendor gets a
hand-tuned path that another vendor's declaration cannot be read against.

A declaration says which surface is *preferred*; it is not a promise that the
account can reach it. Four separate gates can demote a cell to
``generic_surface``, and each one records why:

* ``undeclared_surface`` -- the vendor ships no domain surface here.
* ``capability_not_advertised`` -- the provider's capability metadata does not
  list the surface.
* ``missing_required_context`` -- the caller did not supply the context the
  surface needs, so issuing the call would be malformed.
* ``entitlement_denied`` -- the surface exists and is advertised, but this
  account's plan tier is not entitled to it. Capability metadata is a static
  declaration of what the vendor ships, not of what the key has bought, so this
  gate is fed by observation: ``SearchProvider.search_domain`` retries a refused
  surface-specific call on the generic arm and, when that retry succeeds,
  records the denial through ``mark_domain_surface_unavailable`` for the rest of
  the run. Operators who already know a plan tier excludes a surface can
  pre-seed the same set instead of paying for the probe.

A demoted cell is ``generic_surface``, never ``failed``: the vendor is not
charged with a retrieval miss for an entitlement we did not buy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    from .providers import ProviderCapabilities

DOMAINS = frozenset({"code_and_pr", "legal", "entity_resolution", "gtm"})
PROVIDERS = frozenset({"exa", "parallel-web", "firecrawl", "brave", "tavily", "perplexity"})
GENERIC_SURFACE = "generic_surface"

# Why a cell fell back to the generic arm. Persisted in the surface record so
# the report can tell "this vendor ships nothing here" apart from "this key is
# not entitled here" -- they mean opposite things for a specialization claim.
DOWNGRADE_UNDECLARED = "undeclared_surface"
DOWNGRADE_NOT_ADVERTISED = "capability_not_advertised"
DOWNGRADE_MISSING_CONTEXT = "missing_required_context"
DOWNGRADE_ENTITLEMENT_DENIED = "entitlement_denied"
DOWNGRADE_UNKNOWN_VARIANT = "unknown_variant_value"


class DomainSurfaceError(ValueError):
    """Raised for an undeclared provider/domain pair or malformed context."""


@dataclass(frozen=True)
class SurfaceVariant:
    """A declared conditional surface, selected by a context value."""

    surface_id: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SurfaceDeclaration:
    provider_id: str
    domain: str
    surface_id: str | None
    operation: str
    endpoint: str
    options: Mapping[str, Any]
    context_option: str | None = None
    # Context keys the surface cannot be called without. Absent any one of
    # them the cell degrades to the generic arm rather than issuing a request
    # the vendor will reject and that scoring would read as vendor weakness.
    required_context: tuple[str, ...] = ()
    # Optional context keys passed through to the surface when supplied.
    optional_context: tuple[str, ...] = ()
    # Context key whose value selects a declared variant, if any.
    variant_key: str | None = None
    variants: Mapping[str, SurfaceVariant] = field(default_factory=dict)

    def declared_surface_ids(self) -> frozenset[str]:
        """Every surface id this declaration can select, variants included."""
        ids = {self.surface_id} if self.surface_id is not None else set()
        ids.update(variant.surface_id for variant in self.variants.values())
        return frozenset(ids)


@dataclass(frozen=True)
class SurfaceSelection:
    provider_id: str
    domain: str
    surface_id: str
    operation: str
    endpoint: str
    options: Mapping[str, Any]
    generic_surface: bool
    # Which surface the registry preferred, and why it was not used. Both are
    # ``None`` on a specialist selection.
    declared_surface_id: str | None = None
    downgrade_reason: str | None = None

    def record(self) -> dict[str, Any]:
        """Return stable metadata persisted with the provider call/cell.

        ``declared_endpoint`` is the registry's literal. The endpoint actually
        requested is stamped in by the adapter under ``endpoint`` -- the two can
        drift, and "surface used" must report the observation.
        """
        return {
            "provider_id": self.provider_id,
            "domain": self.domain,
            "surface_id": self.surface_id,
            "operation": self.operation,
            "declared_endpoint": self.endpoint,
            # Keys only, matching `sanitize_request`'s posture for the request
            # side. Surface options carry caller-supplied context (source-policy
            # domains, match conditions, entity types); persisting their values
            # here while the request sanitizer reduces to `option_keys` would be
            # an asymmetry that stops being harmless the first time a knob
            # carries an account identifier.
            "option_keys": sorted(str(key) for key in self.options),
            "generic_surface": self.generic_surface,
            "declared_surface_id": self.declared_surface_id,
            "downgrade_reason": self.downgrade_reason,
        }

    def apply(self, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Merge this surface's options into a caller's call options."""
        merged = dict(options or {})
        for key, value in self.options.items():
            if key == "advanced_settings":
                advanced = dict(merged.get(key, {}) or {})
                advanced.update(dict(value))
                merged[key] = advanced
            else:
                merged[key] = value
        return merged


# Every cross-domain control cell is declared, including honest generic arms.
# Parallel's source-policy domains are supplied by each task because primary
# legal sources vary by jurisdiction and hand-tuning a global allowlist would
# bias the suite. The adapter submits FindAll and terminates on the
# acknowledgement; polling the asynchronous result is owned by the domain
# runner rather than masquerading as ordinary web search.
SURFACE_DECLARATIONS: Mapping[tuple[str, str], SurfaceDeclaration] = {
    ("firecrawl", "code_and_pr"): SurfaceDeclaration(
        "firecrawl",
        "code_and_pr",
        "firecrawl.category.developer",
        "search",
        "/v2/search",
        # v2 `categories` takes objects with a `type`; the enum is
        # developer/research/pdf (docs.firecrawl.dev, verified 2026-09-21).
        {"categories": [{"type": "developer"}]},
    ),
    ("firecrawl", "legal"): SurfaceDeclaration(
        "firecrawl", "legal", None, "search", "/v2/search", {}
    ),
    ("firecrawl", "entity_resolution"): SurfaceDeclaration(
        "firecrawl", "entity_resolution", None, "search", "/v2/search", {}
    ),
    ("firecrawl", "gtm"): SurfaceDeclaration("firecrawl", "gtm", None, "search", "/v2/search", {}),
    # No specialist arm. Exa's changelog deprecates the `github` category
    # ("the `pdf`, `github`, and `tweet` search categories are being
    # deprecated", dated 2026-07-23; re-verified against exa.ai/docs/changelog
    # on 2026-09-21), and `/search` documents unknown strings as free-text
    # category *hints* rather than rejecting them. A deprecated category is
    # therefore the worst possible declaration: it never 4xxs, so no downgrade
    # gate can fire, and the cell would persist `surface_id:
    # "exa.category.github", generic_surface: false` while the request actually
    # ran a hint-flavoured generic query -- H1 adjudicated against a surface
    # that no longer exists. `includeDomains: ["github.com"]` was considered and
    # rejected: it is a filter we would author, not a domain surface Exa ships,
    # and inventing one for a vendor biases the very comparison DSB measures.
    # Brave, Tavily and Perplexity make no domain claim; Brave and Tavily ship no domain surface (Tavily's
    # `topic` enum -- general/news/finance -- is a freshness/vertical toggle, not
    # a declared specialist index, and authoring one for them would bias the
    # very comparison DSB measures). They enter every domain as honest generic
    # arms: external baselines the claimants' specialist surfaces must beat.
    ("brave", "code_and_pr"): SurfaceDeclaration(
        "brave", "code_and_pr", None, "search", "/res/v1/web/search", {}
    ),
    ("brave", "legal"): SurfaceDeclaration(
        "brave", "legal", None, "search", "/res/v1/web/search", {}
    ),
    ("brave", "entity_resolution"): SurfaceDeclaration(
        "brave", "entity_resolution", None, "search", "/res/v1/web/search", {}
    ),
    ("brave", "gtm"): SurfaceDeclaration("brave", "gtm", None, "search", "/res/v1/web/search", {}),
    ("tavily", "code_and_pr"): SurfaceDeclaration(
        "tavily", "code_and_pr", None, "search", "/search", {}
    ),
    ("tavily", "legal"): SurfaceDeclaration("tavily", "legal", None, "search", "/search", {}),
    ("tavily", "entity_resolution"): SurfaceDeclaration(
        "tavily", "entity_resolution", None, "search", "/search", {}
    ),
    ("tavily", "gtm"): SurfaceDeclaration("tavily", "gtm", None, "search", "/search", {}),
    # Perplexity is a generic baseline too. Its Search API does ship a
    # `search_type: people` mode, which is a plausible entity_resolution
    # surface, but it is not declared here until its semantics are verified
    # against the suite's company-and-people instances -- an unverified
    # declaration is exactly what the Exa `github` note above warns against.
    ("perplexity", "code_and_pr"): SurfaceDeclaration(
        "perplexity", "code_and_pr", None, "search", "/search", {}
    ),
    ("perplexity", "legal"): SurfaceDeclaration(
        "perplexity", "legal", None, "search", "/search", {}
    ),
    ("perplexity", "entity_resolution"): SurfaceDeclaration(
        "perplexity", "entity_resolution", None, "search", "/search", {}
    ),
    ("perplexity", "gtm"): SurfaceDeclaration("perplexity", "gtm", None, "search", "/search", {}),
    ("exa", "code_and_pr"): SurfaceDeclaration("exa", "code_and_pr", None, "search", "/search", {}),
    ("exa", "legal"): SurfaceDeclaration("exa", "legal", None, "search", "/search", {}),
    ("exa", "entity_resolution"): SurfaceDeclaration(
        "exa",
        "entity_resolution",
        "exa.category.people",
        "search",
        "/search",
        {"category": "people"},
        # `people` and `company` are both in the current `/search` documented
        # category set (exa.ai/docs/reference/search, verified 2026-09-21); the
        # `linkedin` category they replaced is gone. Re-check this date whenever
        # a category declaration changes -- the deprecation above was dated too.
        # Entity resolution covers people and companies; the company arm is a
        # declared variant rather than a code-side override so every surface
        # this pair can select is readable from the registry alone.
        # `entity_type` is Parallel's free-form FindAll vocabulary
        # ("companies", "people"); Exa's categories are a different, closed
        # set. Sharing one catalog key made a company task silently resolve to
        # Exa's people index, so the variant is read from its own key and the
        # accepted spellings are declared rather than assumed.
        variant_key="exa_category",
        variants={
            "company": SurfaceVariant("exa.category.company", {"category": "company"}),
            "companies": SurfaceVariant("exa.category.company", {"category": "company"}),
            "people": SurfaceVariant("exa.category.people", {"category": "people"}),
        },
    ),
    ("exa", "gtm"): SurfaceDeclaration(
        "exa", "gtm", "exa.category.company", "search", "/search", {"category": "company"}
    ),
    ("parallel-web", "code_and_pr"): SurfaceDeclaration(
        "parallel-web",
        "code_and_pr",
        "parallel.search.source_policy",
        "search",
        "/v1/search",
        {},
        context_option="source_policy",
    ),
    ("parallel-web", "legal"): SurfaceDeclaration(
        "parallel-web",
        "legal",
        "parallel.search.source_policy",
        "search",
        "/v1/search",
        {},
        context_option="source_policy",
    ),
    ("parallel-web", "entity_resolution"): SurfaceDeclaration(
        "parallel-web",
        "entity_resolution",
        "parallel.findall",
        "findall",
        "/v1beta/findall/runs",
        {},
        required_context=("entity_type", "match_conditions"),
        optional_context=("match_limit", "generator"),
    ),
    ("parallel-web", "gtm"): SurfaceDeclaration(
        "parallel-web",
        "gtm",
        "parallel.findall",
        "findall",
        "/v1beta/findall/runs",
        {},
        required_context=("entity_type", "match_conditions"),
        optional_context=("match_limit", "generator"),
    ),
}


def generic_surface_selection(
    provider_id: str,
    domain: str,
    *,
    reason: str,
    declared_surface_id: str | None = None,
) -> SurfaceSelection:
    """Build the honest generic arm for a provider/domain pair."""
    return SurfaceSelection(
        provider_id,
        domain,
        GENERIC_SURFACE,
        "search",
        _generic_endpoint(provider_id),
        {},
        True,
        declared_surface_id=declared_surface_id,
        downgrade_reason=reason,
    )


def resolve_domain_surface(
    provider_id: str,
    domain: str,
    capabilities: ProviderCapabilities,
    *,
    context: Mapping[str, Any] | None = None,
    unavailable_surfaces: Iterable[str] = (),
) -> SurfaceSelection:
    """Resolve a declared surface against metadata and observed entitlements."""
    try:
        declaration = SURFACE_DECLARATIONS[(provider_id, domain)]
    except KeyError as exc:
        raise DomainSurfaceError(f"undeclared domain surface: {provider_id}/{domain}") from exc
    if capabilities.provider_id != provider_id:
        raise DomainSurfaceError(
            f"capability provider mismatch: expected {provider_id}, got {capabilities.provider_id}"
        )

    context = dict(context or {})
    denied = frozenset(unavailable_surfaces)
    surface_id = declaration.surface_id
    resolved_options = dict(declaration.options)

    if declaration.variant_key is not None and declaration.variant_key in context:
        # Context is caller-supplied, so the variant key's value may be
        # unhashable (a list, say). Look up only what can be a variant name.
        variant_value = context.get(declaration.variant_key)
        variant = (
            declaration.variants.get(variant_value) if isinstance(variant_value, str) else None
        )
        if variant is None:
            # A *present but unrecognised* variant value must not fall through
            # to the base arm. Silently selecting `exa.category.people` for a
            # company task is worse than a downgrade: the cell would persist
            # `generic_surface: false` with no downgrade reason, and H3
            # adjudication would read a company task as having exercised Exa's
            # people index while the evidence record asserts that as fact.
            # Every other gate in this resolver records a reason; so does this
            # one. An *absent* variant key keeps the base arm, which is the
            # declared default rather than a failed lookup.
            return generic_surface_selection(
                provider_id,
                domain,
                reason=DOWNGRADE_UNKNOWN_VARIANT,
                declared_surface_id=declaration.surface_id,
            )
        surface_id = variant.surface_id
        resolved_options.update(dict(variant.options))

    if surface_id is None:
        return generic_surface_selection(provider_id, domain, reason=DOWNGRADE_UNDECLARED)
    if surface_id not in capabilities.domain_surfaces:
        return generic_surface_selection(
            provider_id,
            domain,
            reason=DOWNGRADE_NOT_ADVERTISED,
            declared_surface_id=surface_id,
        )
    if surface_id in denied:
        return generic_surface_selection(
            provider_id,
            domain,
            reason=DOWNGRADE_ENTITLEMENT_DENIED,
            declared_surface_id=surface_id,
        )

    # A surface that needs context it was not given would issue a malformed
    # call, and the vendor's rejection would be scored as a failed cell. Both
    # the required-context and the source-policy arms degrade instead.
    missing = [key for key in declaration.required_context if key not in context]
    if missing:
        return generic_surface_selection(
            provider_id,
            domain,
            reason=DOWNGRADE_MISSING_CONTEXT,
            declared_surface_id=surface_id,
        )
    for key in declaration.required_context + declaration.optional_context:
        if key in context:
            resolved_options[key] = context[key]

    if declaration.context_option:
        context_value = context.get(declaration.context_option)
        if not isinstance(context_value, Mapping) or not context_value:
            return generic_surface_selection(
                provider_id,
                domain,
                reason=DOWNGRADE_MISSING_CONTEXT,
                declared_surface_id=surface_id,
            )
        resolved_options["advanced_settings"] = {declaration.context_option: dict(context_value)}
    return SurfaceSelection(
        provider_id,
        domain,
        surface_id,
        declaration.operation,
        declaration.endpoint,
        resolved_options,
        False,
    )


def assert_surface_declarations(capabilities: Mapping[str, ProviderCapabilities]) -> None:
    """Assert INTERNAL consistency between the registry and capability metadata.

    Scope, stated precisely because it is easy to over-read: both sides of this
    comparison are authored in this repository. ``metadata.domain_surfaces`` is
    a constructor literal on each adapter, not a live capability probe, so this
    catches a typo between two local constants and nothing more. It is **not**
    vendor parity: a surface the vendor has deprecated (Exa's ``github``
    category) or silently stopped honouring passes this check unchanged,
    because the vendor is never consulted. Only a live conformance run -- one
    request per declared surface against the real API, recording the observed
    status -- can assert the vendor half, and that belongs on demand rather
    than in the offline suite.

    It checks the one direction metadata can prove: a surface the adapter
    advertises must be declared in the registry. The reverse is deliberately
    allowed -- a declared surface the metadata omits is a legitimate
    ``generic_surface`` downgrade, and an entitlement the *account* lacks is not
    visible in metadata at all (see the module docstring's ``entitlement_denied``
    gate, which is fed by observation rather than by declaration).
    """
    if set(capabilities) != PROVIDERS:
        raise DomainSurfaceError(
            f"capability providers must be {sorted(PROVIDERS)}, got {sorted(capabilities)}"
        )
    if set(SURFACE_DECLARATIONS) != {
        (provider, domain) for provider in PROVIDERS for domain in DOMAINS
    }:
        raise DomainSurfaceError("surface registry must declare every provider/domain pair")
    declared_by_provider: dict[str, set[str]] = {provider_id: set() for provider_id in PROVIDERS}
    for (declared_provider, _), declaration in SURFACE_DECLARATIONS.items():
        declared_by_provider[declared_provider].update(declaration.declared_surface_ids())
    for provider_id, metadata in capabilities.items():
        if metadata.provider_id != provider_id:
            raise DomainSurfaceError(
                f"capability provider mismatch: expected {provider_id}, got {metadata.provider_id}"
            )
        unknown = metadata.domain_surfaces - declared_by_provider[provider_id]
        if unknown:
            raise DomainSurfaceError(
                f"undeclared capability metadata for {provider_id}: {sorted(unknown)}"
            )


# DSB-01's claim registry names each vendor by a short claimant name while the
# adapters use their provider id. DSB-07/08 must join claim -> provider ->
# surface, so the mapping is declared here, once, and asserted -- otherwise
# `parallel` and `parallel-web` simply fail to join at report time instead of
# failing loudly at validation time.
CLAIMANT_PROVIDER_IDS: Mapping[str, str] = MappingProxyType(
    {"firecrawl": "firecrawl", "parallel": "parallel-web", "exa": "exa"}
)


def provider_id_for_claimant(claimant: str) -> str:
    """Map a claim registry claimant onto its provider id."""
    try:
        return CLAIMANT_PROVIDER_IDS[claimant]
    except KeyError as exc:
        raise DomainSurfaceError(
            f"unknown claim registry claimant: {claimant!r}; "
            f"expected one of {sorted(CLAIMANT_PROVIDER_IDS)}"
        ) from exc


def assert_claim_registry_vocabulary(claims: Iterable[Mapping[str, Any]]) -> None:
    """Assert every claim joins onto a known provider and a declared domain."""
    for claim in claims:
        claimant = str(claim.get("claimant", ""))
        provider_id = provider_id_for_claimant(claimant)
        if provider_id not in PROVIDERS:
            raise DomainSurfaceError(
                f"claimant {claimant!r} maps to unknown provider {provider_id!r}"
            )
        domain = str(claim.get("domain", ""))
        if domain not in DOMAINS:
            raise DomainSurfaceError(
                f"claim {claim.get('hypothesis_id')!r} names undeclared domain {domain!r}; "
                f"expected one of {sorted(DOMAINS)}"
            )


def unsatisfied_surface_context(
    domain: str, surface_context: Mapping[str, Any] | None
) -> list[str]:
    """Declarations in ``domain`` whose context this task cannot satisfy.

    A task that cannot supply a declaration's context resolves that vendor to
    the generic arm while its competitors reach their specialist surfaces --
    the SPEC risk-table entry verbatim ("Comparing a specialist surface against
    a generic one rigs the comparison"). The `generic_surface` marking makes
    such a run *look* honest while the comparison is already decided, which is
    worse than an unmarked fallback rather than better, so this is checked at
    validation time instead of being discovered in a report.
    """
    context = dict(surface_context or {})
    unsatisfied: list[str] = []
    for (provider_id, declared_domain), declaration in SURFACE_DECLARATIONS.items():
        if declared_domain != domain or declaration.surface_id is None:
            continue
        missing = [key for key in declaration.required_context or () if key not in context]
        option = declaration.context_option
        if option is not None:
            # Presence is not enough. ``resolve_domain_surface`` demotes on a
            # value that is not a non-empty mapping, so a context carrying
            # ``source_policy: {}`` would pass a key-presence check and still
            # strand this vendor on the generic arm -- marked honestly, while
            # the comparison was already decided. Mirror the resolver's gate.
            value = context.get(option)
            if not isinstance(value, Mapping) or not value:
                missing.append(option)
        if missing:
            unsatisfied.append(f"{provider_id}/{domain} needs {sorted(set(missing))}")
    return unsatisfied


def _generic_endpoint(provider_id: str) -> str:
    return {
        "firecrawl": "/v2/search",
        "exa": "/search",
        "parallel-web": "/v1/search",
        "brave": "/res/v1/web/search",
        "tavily": "/search",
        "perplexity": "/search",
    }[provider_id]


__all__ = [
    "CLAIMANT_PROVIDER_IDS",
    "DOMAINS",
    "DOWNGRADE_ENTITLEMENT_DENIED",
    "DOWNGRADE_MISSING_CONTEXT",
    "DOWNGRADE_NOT_ADVERTISED",
    "DOWNGRADE_UNDECLARED",
    "DOWNGRADE_UNKNOWN_VARIANT",
    "GENERIC_SURFACE",
    "PROVIDERS",
    "SURFACE_DECLARATIONS",
    "DomainSurfaceError",
    "SurfaceDeclaration",
    "SurfaceSelection",
    "SurfaceVariant",
    "assert_claim_registry_vocabulary",
    "assert_surface_declarations",
    "provider_id_for_claimant",
    "generic_surface_selection",
    "resolve_domain_surface",
    "unsatisfied_surface_context",
]
