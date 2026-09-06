# LEG-40 — TUI Tests menu: run the gates and watch them live
Status: PROPOSED
Depends on: LEG-17 (`dbt test` must exist first — LEG-39 was merged into it)

## Context

`dbt tui` exposes 13 actions (profile, flash kernel/sysbin/SD, monitor,
build all/app, bspgen, board, port). It exposes NO test action: `tui.py`
contains zero occurrences of qemu, pytest, test or fuzz.

The plumbing is already right, which is why this is additive and not
architectural: `_stream()` (`tui.py:1379`) runs a `Popen` and pushes output
line-by-line into a `RichLog` (`:1274`) from an `@work(thread=True)` worker.
That IS "see the build and the result live". The TUI never implements an
operation itself — it shells to `dbt` (`self._stream([sys.executable, dbt,
"buildall"])`). So it needs LEG-17's `dbt test` to exist before it can call it.

## Scope

1. A **Tests** section in the action menu, bound to keys not already taken
   by the 13 existing bindings (`t` is free; verify the rest at
   implementation time against `tui.py:1242-1254`).
2. Actions: run host suites, Python suites, QEMU bench, fuzz — and "run
   all". Each is an `@work(thread=True, exclusive=True)` worker calling
   `dbt test <gate>` through the existing `_stream()`.
3. Live result, not just live log: a per-gate status the user can read at a
   glance (pending / running / pass / fail / skipped) without parsing the
   scrollback.
4. A gate whose tool is missing shows SKIPPED and names `dbt doctor` —
   never silently absent, never shown as passing.
5. **The QEMU gate is not merely unwired — it is unreachable without
   clobbering the developer's board.** `dbt qemu` does not write
   `.duneos_board`; it READS it and REFUSES when `--board` differs
   (`qemu.py:925`), instructing the user to `echo <qemu board> >
   .duneos_board`. So the gate cannot run from the TUI, or from any
   automation, without overwriting the setting that selects the physical
   board — the exact thing a reviewer refused to do to a live cardputer
   setup during the LEG-34 cycle.
   This spec must make the QEMU gate runnable WITHOUT mutating
   `.duneos_board`: `dbt qemu --board X --build-dir Y` should be
   self-sufficient, the file consulted only as the default when `--board`
   is absent. The guard exists for a real reason (CMake aborts when the
   board changes without a full clean, and `.duneos_board` is read on every
   configure), so the fix is a separate build directory per board, not the
   removal of the check. Resolve this before the TUI action, or the action
   ships as a trap.

## Acceptance criteria

- [ ] Every new binding is free before this change; asserted by a test that
      reads BINDINGS and fails on a duplicate key.
- [ ] Each gate's worker is `exclusive=True` so two runs cannot interleave
      into one RichLog.
- [ ] A failing gate leaves a visibly failed status, not merely a non-zero
      line buried in the log.
- [ ] `dbt qemu --board <qemu board> --build-dir <dir>` succeeds while
      `.duneos_board` names a DIFFERENT board, and leaves that file
      byte-identical. Test asserts content before and after, with the file
      set to a physical board throughout.
- [ ] The cross-board guard still fires where it must: a build directory
      configured for one board must still refuse a different one.
- [ ] A missing tool renders SKIPPED, and the summary is not green.
- [ ] `pytest tools/dbt/tests -q` green; TUI tests run headless (no tty).

## Out of scope

- Any change to what the gates do (LEG-17 owns the runner) — EXCEPT the
  `.duneos_board` coupling above, which is in scope precisely because the
  gate cannot be exposed safely without it.
- Editing/authoring tests from the TUI.
- Coverage reporting.

## Risks

- Textual workers + a long QEMU run: the UI must stay responsive and the
  run must be cancellable, or the TUI becomes worse than the shell.
- Testing a TUI headless is its own cost; if it dominates the diff, say so
  rather than dropping the assertions.
