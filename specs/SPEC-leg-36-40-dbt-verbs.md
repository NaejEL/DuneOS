# LEG-36/40 — Delete the verb that only fails, add the action that is missing
Status: APPROVED

Supersedes the LEG-36 row's framing and `specs/SPEC-leg-40-tui-tests-menu.md`.
One branch, one cycle.

**Theme check, honest:** "a `dbt` verb does something other than its name
promises" holds for LEG-36 and **not** for LEG-40, which is a missing action, not
a misnamed one. They share a branch for size, not for cause. Do not write a
shared narrative into the code.

## Context

**LEG-36.** `flashimg.py::_stage()` has two modes: `profile=None` stages every app
with a built ELF; `profile=<dict>` stages only `apps_flash`. Three entry points
use the profile-less mode — `dbt flash sysbin` and `dbt flashimg`
(`cli.py:541-554`, `:600-613`, both `cmd_flashimg`) and the TUI's `flash-sysbin`
action (`tui.py:1540-1584`, which imports `_stage`/`_create_image` directly).
Every deliberate caller uses profile mode: `cmd_system_flash` (`cli.py:376-407`)
and `qemu._build_sysbin` (`qemu.py:851-882`, which synthesises a one-app profile).

Row figures checked against the tree, and partly wrong:
- 58 app manifests exist. **"71 files" is an image *file* count** (daps + icons +
  `/etc` + both `init.yaml`), not an app count, and it varies with build state.
- **1263 KiB / 500 KiB come from gitignored ELFs** — not checkable from a clean
  tree. Do not re-assert them in code or tests.
- "35 apps" matches `profiles/cardputer-contest/profile.yaml`, which has **36
  entries, 35 unique — `tail` is listed twice** (`:23`, `:40`). `compute_image_sizes`
  double-counts its bytes; `check_profile` reports 36.
- The size is knowable before writing: `system.parse_partition_sizes()`
  (`system.py:260`) reads the real `sysbin` size from `partitions.csv`, while
  `flashimg.py:29` hardcodes `_SYSBIN_SIZE = 0x100000` (the ROADMAP:639
  duplicate). Nothing compares staged bytes to either before `LittleFSError -28`.
- **`--safe` is a victim of the same defect, not an exception.**
  `_stage(safe_mode=True, profile=None)` writes a usb_shell-only `init.yaml`
  **and still stages every built app.** `README.md:189` documents
  `dbt flash sysbin --safe` as the recovery path; `cardputer-recovery` and
  `t-embed-recovery` are the working, profile-expressed equivalents.

**LEG-40.** `tui.py` `_MENU` (`:1207-1232`) and `BINDINGS` (`:1241-1255`) carry 13
actions, none of them tests. Both recorded blockers are **stale**: `dbt test`
exists (`testing.py`, registered `cli.py:650-663`), and the QEMU problem was
resolved the other way round — `testing._probe_qemu()` reports the gate
**unavailable** when `.duneos_board` names no QEMU board, never writes the file,
and maps `qemu.EXIT_CONFIG` (6) to exit 3. **SPEC-leg-40 §5 and its criteria 4-5
are obsolete and must not be built.**

`tools/dbt/tests/test_tui_sdkconfig_guard.py` is the headless pattern to follow.
`ci.yml:51` `FLOOR=436`.

## Product Owner decisions

1. **`dbt flash sysbin` is deleted.** Its only behaviour distinct from
   `dbt system flash` is to ignore the profile and stage everything in `build/`,
   which is the behaviour that produces the oversized image. A command whose only
   remaining specificity is to fail is not repaired.
2. **The ruling covers `dbt flashimg` too** — same function, same defect, and it
   is the verb `CLAUDE.md` and `README.md` actually document. It does **not**
   cover the `flashimg.py` module: `/init.yaml.safe` staging (`:262-265`), `/etc`
   provisioning and icon install are load-bearing for `dbt system flash` and
   `dbt qemu`. Narrowest honest deletion: **the two profile-less CLI verbs and
   the `profile=None` branch of `_stage`.**
3. **The three stranded boards get mechanical profiles.** `esp32s3-devkitc`,
   `esp32c3-devkitc` and `esp32p4-devkitm` have a `boards/<board>/init.yaml` and
   no profile; deleting the profile-less path would leave them unflashable.
   Derive each profile from its existing `init.yaml`, at constant behaviour, so
   "everything goes through a profile" becomes true with no exception.

## Scope

**LEG-36**

- `cli.py` — remove the `flash sysbin` and `flashimg` subparsers; each leaves a
  stub exiting non-zero naming `dbt system flash` (and `dbt system use`).
- `flashimg.py` — `profile` becomes required on `_stage`; delete the
  `profile=None` branch and the `safe_mode` flag with it. Keep `_SAFE_INIT_YAML`
  for the unconditional `/init.yaml.safe` staging.
- `flashimg.py` — replace `_SYSBIN_SIZE` with the size read from
  `boards/<board>/partitions.csv` (reuse `system.parse_partition_sizes`); fail by
  name when the board declares no `sysbin` partition rather than falling back.
- `flashimg.py` — pre-flight before `_create_image`: sum the staged tree, compare
  against the partition size, and on overflow exit with **one** message naming
  partition size, staged size, profile and board. Wrap the LittleFS write so a
  residual `LittleFSError` (metadata the byte sum cannot predict) surfaces as the
  same named error, never a traceback.
- `system.py` — de-duplicate `apps_flash`/`apps_sd` in `compute_image_sizes` and
  `check_profile` so the reported figures and the pre-flight agree; **warn** on
  the duplicate rather than silently collapsing it. Fix `cardputer-contest`'s
  repeated `tail`.
- **No `--safe` on `dbt system flash`.** Recovery is a profile; documentation
  redirects to `dbt system flash --profile <board>-recovery`.
- `profiles/` — add profiles for the three boards of PO decision 3, derived
  mechanically from each `init.yaml`. QEMU boards need none (`qemu.py`
  synthesises its own).
- `tui.py` — re-point `flash-sysbin` at `dbt system flash` through `_stream()`
  (drop the direct `_stage`/`_create_image`/`_find_esptool` imports); relabel to
  name the profile.
- Docs, same commit: `README.md:54,72,84,152,184,189,222`, `CLAUDE.md:22,42`,
  `CONTRIBUTING.md` if it names the verb, `docs/testing.md`, the
  `boards/m5stack-cardputer/etc/*/config.yaml*` comments,
  `apps/user/splash/splash.c:7`, `libdune.h:174`,
  `profiles/cardputer-recovery/profile.yaml:5`, ROADMAP LEG-36 → DONE.
  **`docs/adr/*` is not rewritten** (023, 039 record what was true when decided),
  nor are the ROADMAP historical Phase-25 checkboxes.

**LEG-40**

- `tui.py` — a Tests section in `_MENU`, two entries on free keys (verify against
  the 13 current bindings at implementation time): one runs `dbt test`, one runs
  `dbt test --fuzz --qemu`. **No `--allow-missing` from the TUI.**
- Each is an `@work(thread=True, exclusive=True)` worker whose whole body is
  `_stream([sys.executable, dbt.py, "test", *flags])`. **Thin by design**: gate
  selection, probing, verdict and exit codes live in `testing.py`, which is
  tested. A second implementation in the TUI is a second thing to keep honest,
  and an unavailable gate that renders as a pass is what the last cycle removed.
- Exit-code rendering extracted as a **pure function** (rc → label + style):
  0 pass, 1 failed, **3 = could not run**, distinct wording pointing at the
  reason `run_gates` already printed; anything else failed. **Exit 3 must never
  render as a failure.**
- Cancellation: `_stream()` gains a way to terminate its child, bound to a key
  while a gate runs, so a 180 s QEMU run is interruptible. A cancelled run
  renders as cancelled — neither pass nor fail.
- The TUI must not pass `--board` and must not write `.duneos_board`.
- `specs/SPEC-leg-40-tui-tests-menu.md` — mark §5 and criteria 4-5 obsolete with
  a pointer to `testing.py`; ROADMAP LEG-40 → DONE, second blocker clause struck.

## Acceptance criteria

All Python tests in `tools/dbt/tests/`, fixtures under `tmp_path` —
`partitions.csv` and app ELFs are gitignored and the root `conftest.py` read
guard fails any test that opens them.

1. `dbt flash sysbin` and `dbt flashimg` exit non-zero with a message containing
   `dbt system flash`; `flash --help` no longer lists `sysbin`.
2. `_stage` cannot be called without a profile: `inspect.signature` shows no
   default, and `None` raises rather than staging.
3. Every remaining caller passes a profile — `cmd_system_flash` and
   `qemu._build_sysbin` both reach a monkeypatched `_stage` with a dict.
4. `/init.yaml.safe` survives: staging a synthetic one-app profile into
   `tmp_path` produces it with the usb_shell entry.
5. Recovery without the deleted flag: `dbt system flash --profile
   cardputer-recovery` resolves, validates and stages exactly one app.
6. Every non-QEMU board with an `init.yaml` is named by some
   `profiles/*/profile.yaml`. Tracked YAML only.
7. **Overflow is a named error, not a traceback.** A staging tree larger than a
   constructed `partitions.csv` `sysbin` size exits non-zero with one message
   naming partition size, staged size, profile and board, and `LittleFSError`
   appears nowhere in the output. Assert `SystemExit`, not an uncaught exception.
8. A residual LittleFS ENOSPC — passing the byte pre-flight, failing once
   metadata is written — yields the same named error. Monkeypatch the binding.
9. The partition size comes from `partitions.csv`: a constructed non-1 MiB
   `sysbin` produces an image of that size, and `0x100000` no longer appears as a
   sysbin size literal in `flashimg.py`.
10. A board declaring no `sysbin` partition is refused by name, not by falling
    back to `0x190000`.
11. Duplicate app names count once: `compute_image_sizes` reports the app once
    and `check_profile` warns.
12. `tui.py` no longer imports `_stage`/`_create_image`/`_find_esptool`, and the
    sysbin worker's argv is `[sys.executable, …/dbt.py, "system", "flash", …]`.
13. TUI bindings stay unique and the three tables agree: no duplicate key in
    `BINDINGS`, and the action-id sets of `_MENU`, `BINDINGS` and `action_do`'s
    dispatch are equal. Class attributes only, no Textual app instantiated.
14. The test actions are thin shells: argv is exactly `[sys.executable, …/dbt.py,
    "test"]` and that plus `--fuzz --qemu`; neither contains `--board` nor
    `--allow-missing`.
15. **Exit 3 does not render as failure.** The rc→status mapping returns a
    distinct "could not run" label for 3, differing from the 1-label and
    containing no "fail".
16. A running gate is cancellable: the cancel path terminates the child and
    reports cancelled, neither pass nor fail. **Correction, verified during the
    cycle:** the "not testable headlessly" caveat this criterion shipped with was
    wrong. Textual's `App.run_test()` drives a real event loop — key routing,
    live cancellation of a real child, and the three status renderings were all
    exercised that way. Only terminal-level presentation on a real tty (footer
    layout, colour on a given terminal) stays unproven.
17. `.duneos_board` is untouched by a TUI test run: argv carries no board flag
    and the new code performs no write to `BOARD_FILE`.
18. Suite green; `FLOOR` raised by exactly the number of tests added, same commit.
19. **No tracked doc names a deleted verb as a command.** A test greps
    `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `docs/testing.md` and
    `profiles/**/*.yaml` for `flash sysbin` / `dbt.py flashimg` / `dbt flashimg`
    and fails on any hit. `docs/adr/` and the ROADMAP historical sections are
    excluded deliberately.

## Out of scope

Kernel, HAL, loader, `libdune`, ABI — no `DUNEOS_ABI_VERSION` change.
bspgen-generated files are read, never written; `duneos-bspgen.py`'s
`_SYSBIN_SIZE` stays — it is the producer, `flashimg.py` becomes the consumer.
`testing.py` and `qemu.py` behaviour, the `EXIT_CONFIG`/`UNAVAILABLE_RC` mapping
and the `.duneos_board` guard are settled and untouched — in particular **do not
implement SPEC-leg-40 §5**. `docs/adr/*`. `dbt system deploy`/`diff`, the profile
editor screen, `/etc` and icon staging logic. Test authoring or coverage
reporting from the TUI. Any on-hardware flash as a gate.

## Risks

- **Deleting a verb people type**, including CI scripts outside this repo.
  Criterion 1's stub naming the replacement, not a bare argparse "invalid choice".
- **Recovery regression.** `--safe` disappears. Cardputer and t-embed already
  have a recovery profile; a board without one loses its one-command way back.
- **`dbt system size` is a projection, not the image** — raw ELF bytes, ignoring
  icons, `/etc` and LittleFS metadata. A pre-flight built only on its numbers
  would still let ENOSPC through. Hence criteria 7 **and** 8.
- **Read guard (LEG-38).** `partitions.csv` and `build/*/app.elf` are gitignored;
  any size assertion must construct them. This repo paid for that twice.
- **Textual headless limits.** Bindings, dispatch, argv builders and the rc→status
  mapping are plain functions and testable; a live worker, key routing and
  rendering are not. Criterion 16 states the boundary rather than pretending.
- **Floor drift** — same-commit raise is criterion 18.
- No memory, PSRAM, heap or ABI impact: tooling only, so LEG-37 does not bite.
  But the new image path decides what a physical CardPuter boots, so a real
  `dbt system flash` on hardware before merge is prudent even though no criterion
  can automate it.

## Open questions

None.
