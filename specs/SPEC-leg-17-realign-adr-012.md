Status: PROPOSED

# SPEC-leg-17 — Realign ADR 012 with the test strategy actually in force

## Context

Finding LEG-17 (major, S): `docs/adr/012-test-strategy.md`, at status Accepted, decides to adopt the
Greatest framework vendored into `third_party/greatest/`, a `dbt test` subcommand, a
`kernel/duneos_kernel/tests/` layout and a `docs/testing.md` document.

None of these exists:

- `third_party/` contains only `cjson` and `littlefs`;
- `tools/dbt/cli.py` registers no `test` subparser;
- `docs/testing.md` is absent;
- the actual tests live in `tests/host/` and rely on a homegrown `tassert.h`.

An ADR at status Accepted describing a reality that does not exist is worse than no ADR at all: it
actively steers the reader — human or agent — towards phantom infrastructure. The repository holds
40 ADRs; their collective authority rests on each one being reliable.

## Scope

Restore consistency between ADR 012 and the code, in one direction or the other. Two outcomes are
acceptable and the choice belongs to the maintainer:

- **(A)** implement what the ADR decides (vendor Greatest, add `dbt test`, create the layout and
  `docs/testing.md`);
- **(B)** acknowledge the decision was not followed: move ADR 012 to Superseded or Rejected status,
  and write a successor ADR describing the strategy actually in force (`tests/host/` + `tassert.h` +
  `make -C tests/host test`, and pytest for the Python tooling).

## Acceptance criteria

1. `docs/adr/012-test-strategy.md` does not remain at status Accepted while describing absent
   infrastructure: either each of its elements exists (outcome A), or its status is changed and
   points to its successor (outcome B).
2. If outcome A is chosen: `third_party/greatest/` exists, `python tools/dbt.py test` runs and
   executes the test suite, `docs/testing.md` exists, and the test layout described by the ADR is in
   place.
3. If outcome B is chosen: a successor ADR exists, describes the actual test location, the harness
   actually used and the command to run it, and ADR 012 references it explicitly.
4. In both cases, the test command documented in the governing ADR is executable as written from a
   clean checkout and returns a zero exit code on the current commit.
5. No other ADR references ADR 012 in a way that became false after the change: cross-references are
   checked and corrected.

## Out of scope

Writing new tests (handled by LEG-19); removing the YAML parser copy (handled by LEG-18); a
consistency review of the other 39 ADRs; correcting the ADR count in the README (handled by LEG-22).

## Risks

Outcome A is markedly more costly than outcome B and reopens an architectural decision; outcome B is
cheap but requires stating in writing that a decision taken was not followed. The choice commits the
project's test strategy and must not be made by default, in either direction.

## Open questions

Outcome A or outcome B? The decision belongs to the maintainer and determines which of criteria 2 or
3 applies.
