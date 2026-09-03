Status: PROPOSED

# SPEC-leg-08 — Run the Python tests in CI

## Context

Finding LEG-08 (major, XS): `tools/dbt/tests/test_bspgen_uart.py` is the repository's only Python
test and is run by no job. `grep -rn "pytest" .github/` returns nothing; the `host-tests` job of
`.github/workflows/ci.yml` is limited to `make -C tests/host test`.

The test therefore exists but protects nothing: it can break without CI noticing. This is all the
more costly because `tools/` carries the entire build and flash chain (bspgen, manifest,
capabilities, flashimg): a regression there breaks image production without any C test noticing.

This finding is a prerequisite of LEG-19, which will add tests over the pure logic in `tools/`:
there is no point writing those tests before CI runs them.

## Scope

Add pytest execution to CI, over the existing Python test corpus.

## Acceptance criteria

1. `.github/workflows/ci.yml` includes a step that installs pytest and runs the repository's Python
   test suite.
2. On the current commit, that step runs and `test_bspgen_uart.py` is collected and passes: pytest
   output reports at least 1 test run, 0 failures.
3. A deliberately failing Python test makes the CI job fail (non-zero exit code), and therefore the
   whole workflow.
4. Test collection is explicit about its root (path or configuration), so that a new `test_*.py`
   file added under `tools/` is collected without modifying the workflow.
5. The existing jobs (`host-tests`, `kernel-build`, `apps-build`) keep passing with no behavioural
   change.

## Out of scope

Writing new Python tests (handled by LEG-19); pinning dependency versions (handled by LEG-09); code
coverage; introducing `dbt test` (handled by LEG-17).

## Risks

If collection is too broad, pytest could try to import modules requiring the ESP-IDF toolchain and
fail for reasons unrelated to code quality: criterion 4 calls for an explicit collection root, and
criterion 2 requires observing actual success on the current commit.

## Open questions

None
