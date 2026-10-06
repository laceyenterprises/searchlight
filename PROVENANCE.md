# Provenance

Searchlight was extracted from the search evaluation workbench in a private monorepo
(`modules/search-evaluation-workbench`).

- **Initial snapshot:** source commit `e3af26c43ad7cf98f79fb822e3fa8479368107ae`, exported
  from the committed tree. Only caches were excluded, and source history was not imported.
  Provider vault examples in the source README were replaced with environment references.
- **Changes made in the source after the snapshot were ported here** with their original
  authorship, until the source began consuming Searchlight directly:

| Ported change | What it does |
|---|---|
| CANARYOOM-01 | out-of-model egress qualification for Claude Code code cells |
| SWXCODEXAUTH-01 | canonical codex authentication receipts in host mode |
| GAPPIP-01 | local-only pip commands no longer contaminate GAP cells |
| GAPWHEEL-01 | verifier-only wheels kept out of agent cells |
| VENDORPRICE-01 | Exa and Parallel pricing; per-vendor pricing coverage gate |
| GAPFRESH-01 | post-July 2026 GAP code corpus |

Later fixes were made here first: wheel-cache containment, the pip-exemption audit, and the
native-arm WebFetch pre-approval. The detailed design log is [WORKBENCH.md](WORKBENCH.md).
Catalogs, corpora and fixtures are versioned by hash in each report's `reproduce.md`.
