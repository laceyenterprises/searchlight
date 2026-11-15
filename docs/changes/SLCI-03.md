# Pull-request test scope

Standalone keeps its existing job and publication checks. Pull requests containing
only publication content skip the installed-clone proof and pytest. Code changes
select test files through a static Python import closure, including local scripts
loaded by path. Data inputs select tests through literal path references in that
closure. Selected files run inside the same installed clone, network isolation,
containment environment and parallel pytest runner as the full suite.

The map includes design Python and the glyph import root used by the site builder.
Mapped Python that no test reaches selects no tests for that path and logs why;
an empty selection skips pytest. Broad selections run their selected files.
Shared CI, packaging and unknown inputs, and parse/diff failures retain the full
suite. Decisions and changed-path causes appear in the job log and step summary.
The fixture battery follows its code and fixture
inputs on selected runs. Main pushes retain smoke coverage; scheduled and manual
runs retain the full matrix as the backstop.

## SLCI-04: Conservative handling of unplaced changes

The dependency map recognizes both stripped and repository-qualified Python
module names, so changes to helpers imported through `tests.` or `scripts.`
select their importing tests. Colliding stripped names have no bare registration;
each file keeps its qualified name, and changing either file runs the full suite
with an `ambiguous module name` reason. Building the map no longer needs a reverse
lookup that could fail on a collision.

Report files ending in `.py`, `.sh`, `.js` or `.ts` use normal selection rules.
An executable outside report code/data folders is unmapped and runs the full
suite. Publication content, data ownership and fixture-battery rules are unchanged.
