# SLCI-04: Conservative handling of unplaced changes

The dependency map recognizes both stripped and repository-qualified Python
module names, so changes to helpers imported through `tests.` or `scripts.`
select their importing tests. Colliding stripped names have no bare registration;
each file keeps its qualified name, and changing either file runs the full suite
with an `ambiguous module name` reason. Building the map no longer needs a reverse
lookup that could fail on a collision.

Initializers at stripped import roots retain their repository-qualified package
names, such as `tests`, `scripts`, `design` and `design.glyphs`, rather than
claiming a shared bare `__init__` module. Relative imports in those initializers
resolve within their package, and initializer changes select their importing tests
without spurious ambiguity.

Report files ending in `.py`, `.sh`, `.js` or `.ts` use normal selection rules.
An executable outside report code/data folders is unmapped and runs the full
suite. Publication content, data ownership and fixture-battery rules are unchanged.
