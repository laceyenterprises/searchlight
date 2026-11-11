"""INS: the unique-insight bake-off for search and research APIs.

One question set goes to every arm the same day with frozen request profiles.
Each arm's returned content becomes atomic claims, claims are pooled and
clustered per question, every claim is checked on its own cited page, and the
verified clusters are valued blind. The report credits an arm only for claims
its own source supports, and counts what no other arm found.

``catalog`` loads question sets and arm profiles, ``run`` executes cells under a
spend ledger, ``grade`` extracts, clusters, verifies and values claims, and
``report`` scores a graded run. See docs/insight-bench/README.md.
"""
