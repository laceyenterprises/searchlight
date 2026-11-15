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
