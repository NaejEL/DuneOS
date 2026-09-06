Status: PROPOSED

# SPEC-leg-38 — A test must not depend on a generated file, and CI must prove it

## Context

The same defect has now shipped into two consecutive pull requests, from two different specs, and in
both cases it was caught by a human reading a diff rather than by any gate:

- **PR #5 (SPEC-leg-26)** — `tools/dbt/tests/test_bspgen_uart.py` asserted against
  `boards/*/sdkconfig.board`. That path is gitignored (`.gitignore:47`), so on a clean checkout the
  file does not exist and the test raised `FileNotFoundError`. It passed only on a machine where
  `duneos-bspgen.py` had already run. The PR's first CI run failed on exactly that line.
- **PR #7 (SPEC-leg-37)** — three cases in `tools/dbt/tests/test_sdkconfig_check.py` resolved the
  CardPuter's declared stack through the same `boards/m5stack-cardputer/sdkconfig.board`.
  `declared_config()` skips a missing source silently, so on a clean checkout the declared map was
  simply empty and the assertions failed. SPEC-leg-37 acceptance criterion 6 ("the suite passes")
  therefore did not hold on any machine but the author's.

Two occurrences, two authors, two weeks apart. The shape is identical and it is worth naming: **a
test that reads a build artefact is green on the machine that produced the artefact and red
everywhere else.** It inverts what a test is for — it reports the state of a working directory
rather than the state of the code.

The same PR exposed a second, sharper version of the problem. `test_tui_sdkconfig_guard.py` imports
`dbt.tui`, which imports `textual`, which the `host-tests` job did not install. That is a
collection-time `ImportError`, so pytest aborted the entire run and **all 202 tests went unrun** —
while the job's own summary did not make it obvious. The suite that PR added as its principal safety
argument was producing zero CI signal. A missing dependency is not one red test; it is a silent
absence of testing.

Both failures share a root: **nothing runs the test suite the way a stranger would run it.**

## Scope

Make the two failure modes structurally impossible rather than reviewer-dependent.

1. A CI job that runs the Python test suite on a **pristine checkout** — no `bspgen` beforehand, no
   build directory, nothing generated. A test that needs a generated file fails there, immediately,
   naming itself.
2. A guard that makes a collection error a distinct, loud failure rather than a run that quietly
   tested nothing: assert a minimum collected-test count, or fail the job on any collection error,
   so "0 tests ran" can never read as success.
3. A recorded rule, in `CLAUDE.md`'s "Hard-Won Lessons", stating that a test must construct what it
   asserts against — a fixture, a temporary directory, or a call to the generator inside the test —
   and never read a gitignored artefact.

## Acceptance criteria

1. A CI job runs `pytest tools/dbt/tests` on a checkout where no generated file exists. Verified by
   deleting every path matched by `.gitignore`'s generated-file patterns (`boards/*/board_config.h`,
   `boards/*/sdkconfig.board`, `boards/*/partitions.csv`, `boards/*/idf_target.txt`) before the run.
2. On the current tree that job passes. If it does not, the failing tests are fixed as part of this
   spec — a job that starts red teaches everyone to ignore it.
3. Re-introducing the PR #5 defect makes that job fail: a test reading `boards/*/sdkconfig.board`
   directly is caught, and the failure names the test. Demonstrated, not asserted.
4. A collection error fails the job distinctly from a test failure, with a message saying that no
   test ran. Demonstrated by removing a declared dependency in a scratch copy and showing the job
   fails loudly rather than reporting success.
5. The job asserts a minimum number of collected tests, and that number is derived from the current
   suite rather than a round figure. A run collecting fewer fails.
6. `CLAUDE.md` carries the rule in "Hard-Won Lessons", in the file's existing one-line-lesson style,
   citing both occurrences so the next reader sees it is a pattern and not a hypothesis.
7. The existing `host-tests` job keeps working and its command is unchanged; this spec adds a job
   rather than rewriting the one that guards SPEC-leg-27 and SPEC-leg-08's ground.

## Out of scope

Auditing every existing test for the pattern beyond what criterion 2 forces (the pristine job finds
them by construction); the general Python-CI story owned by `specs/SPEC-leg-08-run-python-tests-in-ci.md`
(deliberately-failing-test gate, explicit collection root); the C host suite under `tests/host/`,
whose Makefile already builds what it asserts against; pinning Python dependency versions
(`specs/SPEC-leg-09-pin-python-dependencies.md`).

## Risks

A pristine-checkout job is slower than the existing one and duplicates part of its work. If it is
made a required check while flaky, it will be worked around rather than fixed — criterion 2 exists
to prevent it starting red, and it should stay a plain job rather than a matrix leg.

The minimum-collected-count assertion of criterion 5 needs updating whenever tests are added, which
is friction. That friction is the point: it is the same self-deleting-marker discipline SPEC-leg-26
used for its expected failures, and the alternative is a suite that can silently shrink to nothing.

## Open questions

None
