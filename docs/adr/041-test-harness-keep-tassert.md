# ADR 041 — Keep `tassert.h`; ship `dbt test` and `docs/testing.md`

**Status:** Accepted · 2026-09-13 · supersedes [012](012-test-strategy.md) in part

## Context

[[ADR-012]] chose **Greatest** as the test framework, a `dbt test` subcommand and a
`docs/testing.md`, all scheduled for Phase 26. Phase 26 has not started. The
testing that did get built arrived through other phases and looks nothing like
the plan: `tests/host/tassert.h`, 41 hand-rolled lines, driving 7 host suites
plus a libFuzzer target under `make`, and a pytest tree for the Python tooling.
No `dbt test`, no `docs/testing.md`.

So ADR 012 is half-contradicted by the tree and half-unbuilt. Leaving it
Accepted means the next contributor reads a framework choice that will not be
honoured and a doc that does not exist.

Vendoring Greatest now would mean rewriting 7 working suites to gain nothing
measurable: every property ADR 012 asked of a framework — header-only, host and
target, tiny — `tassert.h` already has, at 41 lines against ~700. ADR 012
rejected custom macros as "wastes time on tooling instead of tests"; that time
has already been spent, and the tests exist.

## Decision

Clause by clause against [[ADR-012]]:

- **Dropped — Greatest.** `tests/host/tassert.h` is the host harness. Its
  rejection of custom macros stands as a rule for new work: nobody writes a
  second harness.
- **Kept — host-side first, on-device for what genuinely needs hardware.** The
  split is unchanged, and the QEMU bench ([[ADR-039]]) fills the middle ground
  ADR 012 had no name for.
- **Kept — `docs/testing.md`.** Written now rather than in Phase 26, and scoped
  to *how to write a test*. The gate list lives in `CONTRIBUTING.md`, once.
- **Replaced — `dbt test [--target=sim|board]`.** There is no simulator target.
  `dbt test` runs the gates that exist (pytest, host C, opt-in `--fuzz` and
  `--qemu`) and reports each as ran/passed/failed/unavailable.
- **Replaced — the file layout.** ADR 012 put suites next to the unit
  (`kernel/*/tests/`). They are all in `tests/host/`, compiling the shipped
  source through the Makefile; that is where they are and where they stay.
- **Kept — no coverage measurement, no perf suite, no big-bang back-fill.**
- **Superseded by events — "Phase 26 adds the framework".** Fuzzing, which ADR
  012 explicitly deferred, shipped first (LEG-26).

An unavailable gate exits non-zero. A gate that did not run is not a gate that
passed, and the repo has now lost three cycles to green runs that verified
nothing.

## Consequences

- `tassert.h` is load-bearing. Extending it is cheaper than replacing it; a
  third harness needs its own ADR.
- `dbt test` is a fifth thing that can drift from `CONTRIBUTING.md`. A test
  asserts the gate list appears in exactly one tracked file.
- Phase 26 inherits a working harness instead of a framework decision.

## Alternatives

- **Vendor Greatest as written.** Rejected: rewrites 7 green suites for no
  measurable gain, and the ADR's own argument against tooling time now cuts the
  other way.
- **Leave ADR 012 Accepted and unimplemented.** Rejected: an ADR the tree
  contradicts teaches contributors to distrust the folder.
- **Withdraw ADR 012.** Rejected: its host-first reasoning is the reasoning
  still in force. Supersede the clauses that failed, keep the rest.
