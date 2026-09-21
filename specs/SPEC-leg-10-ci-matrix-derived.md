# LEG-10 — Derive the CI board/profile matrix from the tree
Status: APPROVED

Supersedes `specs/SPEC-leg-10-ci-board-profile-matrix.md`, whose criterion 6
("document the reason a board is skipped") is the hardcoded-skip-list
anti-pattern the PO ruling rejects, and whose inventory predates ADR 039.

## Context

CI builds 1 board fully (`m5stack-cardputer`), boots 2 under QEMU, builds 1
profile. Everything else in `boards/` and `profiles/` is unbuilt.

Inventory verified against the tree, not the row:

| board | `cpu` | `arch/<dir>/arch.cmake` | today |
| --- | --- | --- | --- |
| `m5stack-cardputer` | esp32s3 | yes | full build |
| `esp32s3-devkitc` | esp32s3 | yes | — |
| `lilygo-t-embed-cc1101` | esp32s3 | yes | — |
| `esp32s3-qemu` / `-psram` | esp32s3 | yes | build + boot |
| `kincony-A16` | esp32 | yes | — |
| `esp32c3-devkitc` | esp32c3 | **no** | excluded |
| `esp32p4-devkitm` | esp32p4 | **no** | excluded |

Where the tree contradicts the documents:

- `ROADMAP.md:679` says "1 profile out of 7". There are **10** — the last cycle
  added three. It is 1 of 10, 8 in scope.
- **`boards/esp32p4-devkitm/board.yaml` declares no `arch:` key**, and
  `esp_idf.py:132` reads `b.get("arch", "xtensa-esp32s3")`, so dbt currently
  calls a RISC-V board Xtensa. A matrix keyed on `arch:` would put
  `esp32p4-devkitm` in the build list — the very board the ruling excludes.
  **The derivation keys on `board.cpu`**, which every board declares and bspgen
  validates (`duneos-bspgen.py:120-126`).
- **`dbt flash kernel --build-only` is the right CI entry point for every
  board.** `esp_idf.py:201-253` pins `IDF_TARGET` from `idf_target.txt` per
  invocation; the `env -u IDF_TARGET` the last cycle needed was a raw-`idf.py`
  artefact, and `ci.yml:192-196` uses that raw path today. `cmd_flash_kernel`
  also runs bspgen and `sdkconfig_check.enforce`, replacing two workflow steps.
- `dbt system size` returns 0 for missing artefacts and still exits 0
  (`system.py:308-327`). Not usable as a gate outside a job that has built.

## Product Owner decisions

1. **Architectures not yet added are excluded.** `arch/riscv32/` has no
   `arch.cmake` and no `hal/` (LEG-24, Phase 28), so `esp32c3-devkitc` and
   `esp32p4-devkitm` are out of scope. **In scope: 6 boards, 8 profiles.**
2. **No skip list.** A board is in the matrix when `arch/<arch_dir>/arch.cmake`
   exists, with `arch_dir = "xtensa_" + cpu` for `cpu in XTENSA_CPUS`, else
   `riscv32`. Phase 28 adds the arch and the two boards enter CI with no
   workflow edit. A hardcoded exclusion goes stale the day that lands and
   nobody remembers to edit CI.

## Scope

**`tools/dbt/ci_matrix.py`** (new) — the single source of the derivation,
importable and CLI-callable. Reads only tracked files (`boards/*/board.yaml`,
`profiles/*/profile.yaml`, `arch/*/arch.cmake`). Emits:

- `kernel_boards` — buildable boards **not** declaring `qemu: true`. The QEMU
  boards are already built *and booted* by `qemu-smoke`, a strictly stronger
  gate; building them twice buys nothing.
- `qemu_boards` — buildable boards declaring `qemu: true`.
- `check_profiles` — every profile whose board is buildable.
- `build_profiles` — per buildable board, the profile with the most distinct
  apps, when that count is ≥ 1. Today: `cardputer-contest`, `t-embed-default`,
  `kincony-a16-default`. `esp32s3-devkitc-default` lists no apps, so it is
  checked, not built.
- `excluded` — `{board: reason}`, printed by the discover job so the exclusion
  is visible in every run's log rather than buried in a file.

**`.github/workflows/ci.yml`**

- New `discover` job emitting the five outputs to `$GITHUB_OUTPUT`
  (`ubuntu-latest` + python + pyyaml, ~30 s), consumed via `fromJSON`.
- `kernel-build` → matrix over `kernel_boards`, `fail-fast: false`, each job
  writing its own `.duneos_board` on a fresh checkout, body
  `python tools/dbt.py flash kernel --build-only` (replacing the bspgen step and
  the raw `idf.py build`).
- `qemu-smoke` → matrix from `qemu_boards` instead of the literal pair. The
  `/dev/logic0` link gate stays.
- New `profile-check` job — one container, bspgen per in-scope board then
  `dbt system check --profile <p>` for all 8.
- `apps-build` → matrix over `build_profiles`; `dbt system build` then
  `dbt system size` as a non-gating report.
- Fix both `Cache pip` keys to hash `tools/constraints.txt` — what actually
  determines the installed set — rather than `duneos-bspgen.py` / `dbt.py`.

**`tools/dbt/tests/test_ci_matrix.py`** (new) — the gate that stops the gap
reopening:

1. Rule tests on synthetic trees under `tmp_path`: board with and without an
   `arch.cmake`, with and without `qemu: true`, profiles with 0 and N apps.
2. Real-tree completeness: every `boards/*/board.yaml` is in exactly one of
   `kernel_boards`, `qemu_boards`, `excluded`; every `profiles/*/profile.yaml`
   is in `check_profiles` or belongs to an excluded board.
3. Workflow wiring: `ci.yml` parses as YAML, the board and profile matrices are
   `fromJSON(needs.discover.outputs.…)` expressions, and **no board or profile
   name appears as a literal matrix value anywhere in the file**. A future
   hardcoded list fails here.

**Docs** — `ROADMAP.md:679` to the real figures, DONE, derivation named; the
superseded spec marked.

## Acceptance criteria

1. On the current tree the module returns `kernel_boards = {m5stack-cardputer,
   esp32s3-devkitc, lilygo-t-embed-cc1101, kincony-A16}`, `qemu_boards =
   {esp32s3-qemu, esp32s3-qemu-psram}`, `excluded = {esp32c3-devkitc,
   esp32p4-devkitm}`, `check_profiles` = the 8 non-RISC-V profiles,
   `build_profiles = {cardputer-contest, t-embed-default, kincony-a16-default}`.
2. **A `tmp_path` fixture adding `arch/riscv32/arch.cmake` moves both RISC-V
   boards into `kernel_boards` and their profiles into `check_profiles`, with no
   change to `ci.yml`.** This is the criterion that proves the derivation.
3. A synthetic `boards/<new>/board.yaml` added to a `tmp_path` tree with nothing
   else updated fails the completeness test — asserted directly, not by
   expecting a red run.
4. The wiring test fails when a literal board name is injected into a matrix
   value of a copy of `ci.yml` under `tmp_path`.
5. All 4 `kernel-build` matrix entries green on the branch head. **No entry
   carries `continue-on-error` and none is skipped.**
6. `profile-check` exits 0 for all 8 in-scope profiles; making one profile
   reference a non-existent app turns it red (`system.py:219-224`) —
   demonstrated once locally, not committed.
7. All 3 `apps-build` entries green, each printing a `dbt system size` report.
8. Every generated artefact consumed by CI is produced inside the run: no job
   reads a `boards/*/{sdkconfig.board,partitions.csv,board_config.h,
   idf_target.txt}` it did not generate, and no new pytest reads one (LEG-38).
9. `FLOOR` raised in the same commit as the new tests, to (new ran count − 8).
10. `ROADMAP.md:679` states 8 boards / 10 profiles, 6 and 8 covered, 2 excluded
    by arch derivation. `grep -rn "1 profile out of 7"` returns nothing.
11. Job count and measured cost recorded in the PR from the real run: 6 jobs →
    13. Expected wall clock ≤ 6 min; expected billed ~35-40 min against ~15
    today, the increase being the `espressif/idf:v6.0.1` pull paid once per
    containerised job. **No nightly split** — wall clock is unchanged because
    the added jobs are parallel, and a PR gate is judged on wall clock. If the
    measured billed total exceeds 60 min, state the fallback; do not implement
    it this cycle.
12. Caching verdict recorded: ESP-IDF is not and need not be cached — the
    container image *is* the pinned toolchain. Only the pip keys change.
13. No change to `tools/dbt/` beyond `ci_matrix` and its CLI wiring is needed to
    make any in-scope board build. If one is required, it is reported as a new
    finding before being made.

## Out of scope

`arch/riscv32/` and the two RISC-V boards and their profiles (LEG-24, Phase 28).
Fixing `esp_idf.py:132`'s `arch` default — named here as a trap, it belongs to
LEG-24's cycle, and the `cpu` derivation sidesteps it. bspgen-generated files;
`board.yaml` and `profile.yaml` contents. Kernel, loader, HAL, ABI — zero C
change expected. Hardware testing; `dbt system flash`/`deploy`; adding boards or
profiles. **A hardcoded skip list, `continue-on-error`, or a
documented-exclusion clause — rejected by PO decision 2.**

## Risks

- **A board never built in CI may not build.** That is the LEG-31 defect class
  this spec exists to expose. If one fails, fix it when the fix is confined to
  `board.yaml`/bspgen/CMake; otherwise **stop and report**. Do not add a skip,
  do not mark the entry non-gating.
- **`dependencies.lock:56` pins `target: esp32s3`.** The `kincony-A16` (esp32)
  job makes the component manager re-resolve and rewrite the tracked lock, and
  the esp32-only PHY components (LEG-16) resolve differently per target. Expect
  a dirty tree in that job. Determine whether the lock stays valid per-target or
  needs a per-target artefact — and **do not "fix" it by untracking the file**,
  which LEG-07 tracked deliberately.
- **Container pull per job** is the dominant fixed cost. Grouping boards into one
  sequential job would pay it once but serialise ~12 min of wall clock — the
  worse trade for a PR gate.
- **`fromJSON` matrices are rigid.** A malformed `$GITHUB_OUTPUT` line yields an
  opaque "matrix must be an array". The discover job must print what it emits so
  a bad run is diagnosable from the log.
- **LEG-38.** All derivation reads `board.yaml`; `sdkconfig.board` and
  `idf_target.txt` are generated and would pass only where bspgen has run.
- **`FLOOR` drift** — criterion 9 is not optional.
- No memory or ABI impact: nothing on device changes.

## Open questions

None.
