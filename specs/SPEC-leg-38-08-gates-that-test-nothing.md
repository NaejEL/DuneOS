# LEG-38 + LEG-08 — Gates that report success while having tested nothing
Status: APPROVED

Fuses LEG-38 (tests must not read generated files) and LEG-08 (the gate's own
proof). They are one defect seen from two sides; superseded specs:
`SPEC-leg-38-tests-must-not-read-generated-files.md`,
`SPEC-leg-08-run-python-tests-in-ci.md`.

## Context

Four instances on record. In each, CI was green and nothing had been verified.

1. `test_bspgen_uart.py` (PR #5) and `test_sdkconfig_check.py` (PR #7) asserted
   against `boards/*/sdkconfig.board`, gitignored at `.gitignore:49`. Green only
   where bspgen had run. Caught by a human reading a diff.
2. A collection-time `ImportError` (`dbt.tui` → `textual`) aborted the dbt suite
   with **202 tests unrun** while the job read green.
3. 2026-09-07, LEG-41 PR review: `host-tests` used a bare `actions/checkout@v4`;
   `make` died in 6 s on an empty `third_party/cjson`, so the `pytest` step later
   in the same job never ran. Fixed in `6395ac4`. Evidence, not work.
4. **Three assertions still live and vacuous**, found by this spec's audit. Each
   reads `boards/<name>/sdkconfig.board` behind `if not generated.exists():
   continue`, so it skips wherever the artefact is absent — which is every CI run:
   - `tools/dbt/tests/test_bspgen_qemu.py:94-102`
   - `tools/dbt/tests/test_bspgen_main_task_stack.py:199-207`
   - `tools/dbt/tests/test_bspgen_main_task_stack.py:254-258`

   Their docstrings say "guards against a stale **checked-out** artefact". The
   artefact is not checked out; it is ignored. These have never executed. This is
   the silent form of the defect, worse than instances 1–2: those went red
   somewhere, these are green having tested nothing.

State: no `conftest.py`, `pytest.ini`, `pyproject.toml` or `setup.cfg` anywhere.
Collection root is a bare CLI argument in `ci.yml:42`. No collected-count floor,
no proof the job can go red.

Rest of the suite audited clean: tracked sources or `tmp_path` fixtures only.

## Scope

- `tools/dbt/tests/conftest.py` — read-time guard: opening a gitignored path
  fails the test that did it, naming test and path. Enforced on the path actually
  opened, whatever expression produced it — not a lint of source text. Ignore
  status comes from git itself, not a hand-maintained list. Deny set scoped to
  build artefacts under the repo (never `__pycache__`, `.pyc`, caches). Git
  unavailable → clear skip, never silent pass. **Universal, no opt-out fixture**
  (PO decision: an opt-out is the convention this spec removes).
- `pytest.ini` at repo root (PO decision) — collection root and strictness, so a
  new `tools/**/test_*.py` is collected by bare `python -m pytest`.
- The three vacuous assertions — **rebuilt against `tmp_path`** (PO decision):
  run bspgen on the tracked `board.yaml` into `tmp_path`, compare there. The
  check becomes real and runs in CI. Not deleted.
- `.github/workflows/ci.yml`, dbt pytest step — fatal collected-count floor
  (exit 5 / "0 collected" is a hard failure) and a self-proof step running pytest
  over a generated failing test, failing the job if that exits zero.
- `CLAUDE.md` — extend the existing LEG-38 lesson with instances 3–4 and the
  mechanism. One entry, no new section.
- `ROADMAP.md` — the LEG-38 and LEG-08 rows.

## Acceptance criteria

1. A test opening a gitignored path fails, naming test and path — **including on a
   machine where that file exists**. This is what separates the mechanism from
   `FileNotFoundError`.
2. Tests opening tracked paths are unaffected; suite passes where bspgen has run.
3. Suite passes on a pristine checkout with every generated path removed
   (`boards/*/{board_config.h,sdkconfig.board,partitions.csv,idf_target.txt}`,
   `build*/`, `sdkconfig`). Verified by CI on a fresh checkout with no bspgen step.
4. No path under `tools/dbt/tests/` is built against `REPO_ROOT` for a generated
   artefact. The three offenders regenerate into `tmp_path` and go red when the
   YAML and bspgen's output disagree — verified by mutating the YAML in `tmp_path`.
5. Collection root declared in `pytest.ini`; a new `tools/**/test_*.py` is
   collected by bare `python -m pytest`, no workflow edit.
6. A collection error fails the job with a message stating no test ran. Verified
   by removing `textual` from the CI install list on a scratch branch.
7. Workflow asserts a collected-count floor derived from the actual suite count,
   and fails when fewer are collected. Verified with a `-k` selecting one test.
8. Workflow proves the gate bites on every run: pytest over a generated failing
   test must exit non-zero; the step fails the job if it exits zero. The scratch
   test must be uncollectable by the real run.
9. `CLAUDE.md`'s LEG-38 lesson names all four instances and the mechanism, and no
   longer describes the rule as reviewer-enforced.
10. `host-tests`, `fuzz-elf`, `kernel-build`, `qemu-smoke`, `apps-build` green,
    unchanged. `make -C tests/host test` unchanged and passing.

## Out of scope

`tests/host/` C suites; LEG-09 (dependency pinning); LEG-19 (new logic tests);
LEG-17 (`dbt test`); LEG-10 (CI matrix breadth); bspgen output, kernel, apps, ABI.
The dbt pytest step stays inside `host-tests`.

## Risks

- A global open-time hook can fire inside pytest, yaml or importlib themselves.
  A gate that goes red for reasons unrelated to any test is the worst outcome
  here — people learn to bypass it. Deny set must be build artefacts under the
  repo, not everything git ignores.
- `git check-ignore` per read is slow; a per-session cache is expected, and a
  stale cache lets an offender through.
- The collected-count floor needs updating when tests are added. Accepted: it is
  the only thing that catches a suite silently shrinking.
- Criterion 8's scratch test must be invisible to normal collection, or it
  becomes the failure it detects.
- No kernel, ABI, heap or PSRAM impact.

## Open questions

None.
