Status: DELIVERED
Blocking questions: none
Files scope: lib/elf/elf_parse.c, lib/elf/include/duneos/elf_parse.h, kernel/duneos_loader/src/elf_parse.c (removed), kernel/duneos_loader/include/duneos/elf_parse.h (removed), kernel/duneos_loader/CMakeLists.txt, kernel/duneos_loader/src/loader.c, docs/adr/043-*.md, sdk/app/libappmeta.c, sdk/app/include/duneos/appmeta.h, apps/user/launcher/duneos.yaml, tools/dbt/builder.py, tools/dbt/tests/test_*.py, tests/host/Makefile, tests/host/test_appmeta.c, tests/host/elf_corpus.*, .github/workflows/ci.yml, docs/adr/, ROADMAP.md
Depends on: LEG-25 (pure ELF unit), LEG-26 (corpus + fuzzer), LEG-34 (section extents), LEG-41 — all delivered
Priority: 1

# SPEC — DUP-01: one ELF32 reader for the kernel loader and the app SDK

## Context

`kernel/duneos_loader/src/elf_parse.c` and `sdk/app/libappmeta.c` both parse an
ET_REL ELF32 header, walk the section header table and locate
`.duneos_manifest`. The first was extracted to be host-testable and free of
ESP-IDF, FreeRTOS, cJSON and kernel headers (`elf_parse.h:3-18`); it is covered
by two host suites (`tests/host/test_elf_validate`, `test_elf_scan`), a 23-case
malformed corpus with pinned counts (`elf_corpus.h:95-96`) and a libFuzzer entry
point (`fuzz_elf_validate.c`), and is hardened by LEG-01/34/41. The second has
hand-written field offsets, no test, no fuzz target, and is not compiled on the
host. Its single consumer is `apps/user/launcher` (`launcher.c:128`,
`duneos.yaml` → `$SDK/app/libappmeta.c`), which calls `appmeta_read()` once per
`.dap` found in every scanned directory, on files that come off an SD card.

Per ADR 011 this is robustness against malformed, truncated or foreign files —
not a sandbox: the system already trusts every app it loads.

**What the kernel side checks and the SDK side does not.**

| Check | `elf_parse.c` | `libappmeta.c` |
| --- | --- | --- |
| `EI_CLASS == ELFCLASS32`, `EI_DATA == ELFDATA2LSB` | `:15-22` | absent — 4 magic bytes only (`:126`), then ELF32-LE offsets applied to any ELF |
| `e_type == ET_REL`, `e_machine` | `:23-31` | absent |
| `e_shoff != 0`, `e_shnum` ceiling | `:32-35`, `:191` (`DUNEOS_LOADER_MAX_SECTIONS` 1024) | absent — the fallback loop (`:181-192`) runs up to 65535 iterations, two syscalls each, on FAT/SD |
| image size known at all | `duneos_elf_io_t.size` | absent — no `fstat`, no `SEEK_END` |
| `sh_offset <= size`, `sh_size <= size - sh_offset` (non-wrapping form) | `:89-134` (LEG-34) | absent |
| `shoff + shstrndx * shentsize` overflow | N/A (typed array) | computed in `uint32_t` then cast to `long` (`:138`) — wraps |
| `sh_name < shstrtab_size` | `:144-150`, guaranteed for every section | fast path only (`:166`); the fallback seeks to `shstr_off + sh_name` unvalidated (`:186`), so a crafted name read from anywhere in the file can match `.duneos_manifest` |
| string table terminated by a byte the table does not own | `malloc(sh_size + 1)` (`:215`), contract in `elf_parse.h:142-157` | writes `'\0'` over the table's last byte (`:156`), corrupting the final name |
| `sh_link` bounds and `SHT_SYMTAB → SHT_STRTAB` | `:151-167` | not needed on this path |
| manifest payload size | reject outside `2..4096` (`loader.c:1102`) | silently truncated to 2048 (`:195`), losing every field past the cut |
| `e_shentsize == sizeof(elf32_shdr_t)` | **absent** | `:134` (`>= 40`) |

Measured on the working tree (60 built `app.elf`): max `e_shnum` **18**, max
`.duneos_manifest` **400 B**. So the truncation and the 65535-iteration loop are
latent rather than live today, and the batching fast path (`:146-151`) optimises
a condition ADR 022 removed — its comment still claims "apps carry ~300
sections". `appmeta.h:33` likewise claims shared static scratch buffers; the code
uses `malloc` and the stack.

Both sides are already pure C over libc with injected or local I/O, so one
implementation is reachable. What makes the item non-trivial is the link
boundary: the kernel unit is compiled into the `duneos_loader` IDF component,
while `libappmeta.c` is compiled per app by `tools/dbt/builder.py` with
`-Os -ffreestanding -nostdlib -std=c17 -Wall -Wextra` and linked against
`libdune` only. `builder.py` resolves extra sources as `$SDK/…` (`SDK_DIR`) or
app-relative, and auto-adds `<source dir>/include` to `-I` (`:156-171`); apps
already receive `kernel/duneos_kernel/include` as `SDK_INCLUDE`
(`constants.py`), but `kernel/duneos_loader/include` is on no app's include
path and `$ROOT`-relative sources are not expressible. The kernel side already
compiles a source outside its tree (`duneos_loader/CMakeLists.txt:7`,
`third_party/cjson`), so the symmetrical move is cheap there. `third_party/` is
reserved for git submodules (CLAUDE.md), so it is not a candidate.

Cost side: the shared unit allocates `e_shnum * 40` + `shstrtab + 1` where
today's `libappmeta` allocates only `shstrtab`, so the launcher's transient peak
per file goes from ≈0.6 KiB to ≈1.3 KiB — against the 8192-byte per-app heap its
`duneos.yaml` declares (ADR 025). That budget, not stack depth, is this change's
real constraint.

## Scope

1. `duneos_elf_*` becomes the only ELF32 reader in the tree. `appmeta_read()`
   builds an fd-backed `duneos_elf_io_t` (with the real file size), calls
   `duneos_elf_image_open()` with the loader's own `DUNEOS_LOADER_MAX_SECTIONS`,
   walks section names for `.duneos_manifest`, and keeps its own compact-JSON
   field scan. All hand-written ELF offsets, `rd16`/`rd32`, `read_name` and the
   batching fast path go away.
2. The unit is made reachable from an app build without dragging ESP-IDF,
   FreeRTOS, cJSON or kernel headers into userspace. Placement is settled
   by Q1: `lib/elf/`.
3. `e_shentsize` validation is added to the shared unit (Q4), so the dedup drops
   no check either side had.
4. `libappmeta` gains a host suite compiling the shipped `.c` (docs/testing.md),
   and a pytest index guard keeps the single implementation single.
5. Kernel-side behaviour, signatures and frame sizes stay as they are, beyond
   item 3.
6. The stale comments named in Context (`libappmeta.c:146-151`, `appmeta.h:33`)
   are corrected in the same change, or deleted with the code they describe.

## Acceptance criteria

Host-testable — gates `make -C tests/host test` and `python -m pytest -q`:

1. **No second ELF reader.** New pytest under `tools/dbt/tests/`, reading the
   git **index** (`git ls-files -s` + `cat-file blob`, never the working tree —
   LEG-38, pattern of `test_gates_cover.py`): the identifiers `EH_SHOFF`,
   `EH_SHNUM`, `EH_SHSTRNDX`, `EH_SHENTSIZE`, `SH_OFFSET`, `SH_SIZE_FIELD`
   appear in no tracked file; `duneos_elf_image_open` is *defined* in exactly
   one tracked `.c` and *called* in exactly `loader.c` and `libappmeta.c`.
2. **`libappmeta` is host-compiled.** `tests/host/test_appmeta` is added to
   `TESTS` in `tests/host/Makefile` with a rule compiling the shipped
   `sdk/app/libappmeta.c` and the shared unit (not a copy), under the suite's
   `-Wall -Wextra -Werror`.
3. **Malformed files are rejected.** `test_appmeta` writes every
   `elf_corpus` image to a temp dir and asserts `appmeta_read()` returns
   non-zero for each one the loader's own scan path rejects — i.e. the same
   accept list `test_elf_scan.c:65-71` pins, intersected with "has a manifest
   section". Named cases that must each be rejected and are accepted today:
   `ELFCLASS64`, non-`ET_REL`, `e_shnum > 1024`, `sh_offset`/`sh_size` past EOF
   including the wrapping form, `sh_name` past the string table.
4. **Well-formed files are accepted.** `test_appmeta` builds one well-formed
   manifest-bearing image (Q3) and asserts `appmeta_read()` returns 0 with
   `name`, `icon`, `has_display` and `launchable` matching the embedded JSON.
5. **The JSON field scan is covered.** `test_appmeta` exercises
   `json_str`/`json_bool`/`json_caps_display` through `appmeta_read()` on
   adversarial manifests: absent key, key with a non-string value, value
   truncated at the section end, unclosed `capabilities` array, a `"display"`
   token outside that array (the `sources:` false-match the current comment
   claims to prevent) — each with an asserted expected output, no crash-only
   assertion.
6. **The suite bites.** Per docs/testing.md "Proving a test bites": the PR
   records `make -C tests/host test` exiting non-zero against the pre-change
   `libappmeta.c`, with the failing case names.
7. **Corpus pins stay exact.** If the corpus gains or loses a case or a marker,
   `ELF_CORPUS_EXPECTED_CASES` / `ELF_CORPUS_EXPECTED_XFAILS` are updated in the
   same change; `make -C tests/host test` exits 0 and the only surviving xfail
   is `symtab_size_zero` (LEG-35).
8. **Fuzzer still clean.** `make -C tests/host fuzz-run FUZZ_SECONDS=60` exits 0
   with no artefact written. No second fuzz target is required: criterion 1 is
   what makes the existing one cover the launcher's path.
9. **Tooling change is tested.** Q1(c) requires it: `builder.py` gains a
   repo-root-relative source prefix, and a pytest asserts that the new form
   resolves `lib/elf/elf_parse.c` to the right absolute path and that
   `lib/elf/include` lands on `-I`, with no invocation of `tools/dbt.py` (enter
   through `dbt.cli`/the module under test) and nothing read from a gitignored
   path.
10. **CI floor raised.** `FLOOR` in `.github/workflows/ci.yml:58` is raised by
    the number of pytest cases added, in the same commit.

Build gates:

11. `python tools/dbt.py flash kernel --build-only` passes with **no new
    warning**, and `git diff -M` over the moved unit shows a pure rename plus
    criterion-3's `e_shentsize` check and nothing else — no reflow, no
    refactor, no reordering, so the loader's frame depth is unchanged by
    construction and the LEG-37 class stays closed on the kernel side. The
    loader's documented frame figures (`loader.c:1261-1358`) are either
    unchanged or re-derived in the same change.
11bis. **No stale path reference survives.** `grep -rn 'duneos_loader/src/elf_parse\|duneos_loader/include/duneos/elf_parse'`
    over the tree returns only historical prose (specs already marked
    DELIVERED, ROADMAP entries describing past work). `tests/host/Makefile`,
    `kernel/duneos_loader/CMakeLists.txt` and every live reference point at
    `lib/elf/`. CLAUDE.md's correction rule: a half-corrected tree is worse
    than an uncorrected one.
12. `python tools/dbt.py buildall` builds every app; the launcher links and the
    shared unit compiles clean under the app cflags.
13. **No ABI movement.** No exported symbol and no ABI struct layout changes, so
    `DUNEOS_ABI_VERSION` in `abi.h` is **not** bumped; a reviewer can check the
    diff touches neither `abi.h` nor the export table.
14. **Budgets are measured, not assumed.** The PR records (a) the launcher
    `app.elf` `.text` delta, within the Q5 budget, and (b) the computed peak
    transient heap of one `appmeta_read()` (`e_shnum*40 + shstrtab+1 +
    manifest+1`). If that peak leaves under 2 KiB of headroom in the launcher's
    declared `heap_size`, `apps/user/launcher/duneos.yaml` raises it in the same
    change.

Requires a board — this change alters what a physical board links and the call
depth inside the launcher, so the CONTRIBUTING house rule and LEG-37 apply:

15. `python tools/dbt.py qemu --board esp32s3-qemu --build-dir build-esp32s3-qemu`
    passes (kernel boots, loads a `.dap`).
16. On the **M5Stack CardPuter** (no PSRAM, 8 KiB launcher pool): the launcher
    grid lists the **same entries, same count, same names** as before the
    change, and `klog` shows no `-ENOMEM` from the launcher's slot during the
    scan. Recorded before/after in the PR.
17. **Manual, not a gate** (it reads gitignored build artefacts — LEG-38):
    `appmeta_read()` returns 0 for every `app.elf` under `apps/**/build/`, the
    regression bar SPEC-leg-34 criterion 4 established for this unit. A bound
    that is too tight hides apps from the launcher silently.

Documentation:

18. One ADR recording the kernel↔userspace shared-pure-unit boundary and what
    such a unit may depend on (libc + injected I/O only), per Q2 and ADR 000's
    one-page rule.
19. The DUP-01 row in `ROADMAP.md` section 4ter is marked delivered and links
    this spec.

## Out of scope

- `loader.c`'s `extract_manifest()` and its cJSON/klog/`esp_err_t` body
  (`:1083-1180`): it cannot be shared with userspace, and the manifest **JSON**
  parse stays two implementations by design — cJSON kernel-side, the compact
  scan SDK-side.
- DUP-02 … DUP-09.
- LEG-35 (`symtab_size_zero`) and anything about the symbol table.
- `has_dap_ext()` in `launcher.c` versus `duneos_loader_name_is_app()`
  (`loader_limits.h`) — a real third copy of the extension filter, which the
  `lib/elf/` placement may make trivially closable, but which this spec does
  not require.
- bspgen-generated files (`board_config.h`, `sdkconfig.board`,
  `partitions.csv`, `idf_target.txt`).
- `appmeta_t` layout, the launcher's UI, and ADR 026's `opens` field.

## Risks

- **Launcher per-app heap.** +≈0.7 KiB transient against an 8 KiB pool, on a
  board with no PSRAM; a failed `malloc` inside `duneos_elf_image_open()`
  returns `-ENOMEM` and the app simply disappears from the grid, silently.
  Criteria 14 and 16 exist for this.
- **A tighter parse hides apps.** Any real `.dap` the shared unit rejects where
  `libappmeta` accepted it vanishes from the launcher with no diagnostic.
  Criterion 17 is the bar; the launcher logs nothing today on `appmeta_read()`
  failure (`launcher.c:128`).
- **LEG-37 class.** This is an extraction touching what a board links, and
  under Q1(c) it touches what the **kernel** links too. Kernel stack depth is
  unchanged only if the moved `elf_parse.c` is byte-identical modulo the
  `e_shentsize` check — criterion 11 now requires a rename-pure `git diff -M`
  precisely because the move removes the "untouched compile unit" guarantee the
  drafted default had. The launcher's stack is recomputed by `dbt` (ADR 029), so
  the residual app-side exposure is heap, not stack.
  `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` is armed either way.
- **Moving the file is now the chosen path** (Q1(c), decided by the Product
  Owner). It churns `tests/host/Makefile` rules,
  `kernel/duneos_loader/CMakeLists.txt` and the LEG-25/26/34 specs' path
  references: more diff for the same behaviour, and a correction that must be
  made everywhere at once or not at all. Criterion 11bis is the check. The
  kernel's compile unit moving also puts the LEG-37 risk class on **both**
  sides rather than only the app side, which is why criterion 11 demands a
  rename-pure diff instead of merely "no logic change".
- **Slow I/O amplification.** One `fopen` on the T-Embed with an SD mounted was
  measured at 2.26 s (docs/backlog.md); the shared unit reads the whole section
  header table in one `read` where the current code reads 16 headers at a time,
  so per-file read **count** drops. Not measured on that board by this spec.
- **Measuring before committing.** The index-reading pytest of criterion 1 sees
  the last commit, not the working tree: `git add` before running the gates.

## Open questions — all answered 2026-11-14

- Q1 — Placement of the shared unit. (a) It stays at
  `kernel/duneos_loader/src/elf_parse.c` and `builder.py` gains a repo-root
  relative source prefix plus discovery of the component's `include/`;
  (b) it moves to `sdk/elf/`, where `$SDK/elf/elf_parse.c` works with zero
  tooling change and the kernel pulls it the way it already pulls
  `third_party/cjson`; (c) a new neutral `lib/elf/` referenced by both.
  Default: (a) — no move means the kernel's compile unit, its CMake entry, the
  `tests/host` rules and the loader's measured frame table are untouched, which
  keeps the LEG-37 risk class entirely on the app side; apps already consume a
  kernel include directory (`SDK_INCLUDE`), so the direction is not new.
  Answer: **(c) a new neutral `lib/elf/`** — chosen by the Product Owner over
  the drafted default (a). The unit is owned by neither side: the kernel's
  `duneos_loader` component compiles it out-of-tree the way it already compiles
  `third_party/cjson` (`duneos_loader/CMakeLists.txt:7`), and `builder.py` gains
  a repo-root-relative source prefix so apps reference it too. Cost accepted:
  `tests/host/Makefile` rules, `duneos_loader/CMakeLists.txt` and the
  LEG-25/26/34 path references all churn in this cycle, and the kernel's compile
  unit moves — so the LEG-37 risk class now sits on **both** sides, not only the
  app side. Criterion 11 (kernel stack depth unchanged) therefore hardens from a
  by-construction claim into something the cycle must actually show, and the
  per-file correction rule of CLAUDE.md applies: every path reference to the old
  location is fixed in the same commit, none left behind.
- Q2 — Is an ADR owed? Default: yes, one new ADR. No existing ADR covers a
  compile unit shared by the kernel and userspace; ADR 009 is the driver
  boundary, ADR 014 capability resolution. Without it, the next shared unit
  re-litigates Q1.
  Answer: yes, one new ADR (default applied) — ADR 043, recording that a compile
  unit may be shared by the kernel and userspace, where such a unit lives
  (`lib/`), and what it may depend on (C17 + libc, injected I/O, no ESP-IDF, no
  FreeRTOS, no kernel headers).
- Q3 — The well-formed acceptance fixture. (a) `test_appmeta` builds its own
  manifest-bearing image locally; (b) `elf_corpus.c`'s reference image gains a
  `.duneos_manifest` section so all 23 mutations replay through
  `appmeta_read()`. Default: (a) — (b) shifts every offset the existing cases
  depend on and risks reopening green cases for a non-goal.
  Answer: (a) `test_appmeta` builds its own manifest-bearing image locally
  (default applied) — leaves the 23 pinned corpus offsets untouched.
- Q4 — Add `e_shentsize != sizeof(elf32_shdr_t)` to `duneos_elf_validate()`
  with its own reject enumerator and corpus case? Default: yes. The SDK side
  checks it today (`libappmeta.c:134`) and the kernel side does not; dropping a
  check during a dedup is how this debt class is created. Cost is one compare,
  no new frame.
  Answer: yes (default applied) — new reject enumerator plus its own corpus
  case. A dedup must not drop a check either side had.
- Q5 — Launcher `.text` budget. Default: ≤ +2048 B, any excess measured and
  justified in the PR. No board is close to a flash limit, but an unbudgeted
  number is how size regressions become invisible.
  Answer: ≤ +2048 B (default applied), any excess measured and justified in the
  PR.
- Q6 — `expect_machine` passed by `appmeta_read()`. Default: `0` (no machine
  constraint), preserving today's grid contents; class, data and `ET_REL` are
  enforced, and an arch mismatch is still reported by the loader at launch. The
  SDK has no `CONFIG_IDF_TARGET_*`.
  Answer: `0`, no machine constraint (default applied) — preserves today's grid
  contents; class, data and `ET_REL` are still enforced and an arch mismatch is
  reported by the loader at launch.
- Q7 — `appmeta_read()` return convention. Default: keep `-1` as documented in
  `appmeta.h:30`. ADR 001 governs the kernel; changing the SDK contract would
  touch the launcher for no gain in this spec.
  Answer: keep `-1` (default applied) — `appmeta.h:30` is the SDK contract;
  ADR 001 governs the kernel, not this.
- Q8 — Manifest payload bound. Default: adopt the loader's `2..4096` and
  **reject** above it instead of truncating at 2048. Largest manifest in tree is
  400 B, so nothing observable changes today, and silent truncation is the
  divergence this item exists to remove.
  Answer: adopt the loader's `2..4096` and **reject** above it (default applied)
  — no silent truncation. Nothing observable changes today (largest manifest in
  tree: 400 B).
