Status: APPROVED

# SPEC-leg-34 — Bound every section extent against the image size

## Context

Finding LEG-34 (`BL-ELF-EXTENT`), found by the LEG-26 corpus and confirmed in CI by the LEG-26
fuzzer on 2026-09-06.

`duneos_elf_image_open()` validates the header and the section header table, but nothing bounds a
section's **extent**. `duneos_elf_io_t` (`elf_parse.h:49-52`) carries a `read` callback and a
context and no size at all, so the unit cannot know where the file ends. Two consequences, both
reachable from any `.dap` dropped on the SD card:

1. **An unbounded allocation from untrusted input.** `elf_parse.c:115` is
   `malloc((size_t)ss->sh_size + 1u)` where `sh_size` is a `uint32` read from the file. The CI fuzz
   job found it on `main`: `ERROR: libFuzzer: out-of-memory (malloc(2483027969))` — a 2.4 GiB
   request from a malformed section header. On the target this fails safe (`malloc` returns NULL,
   the loader rejects with `-ENOMEM`), so it is not a crash; it is a memory-exhaustion vector and,
   on a 300 KiB heap, a denial of service that costs an attacker one file.
2. **`sh_offset + sh_size` may wrap past `UINT32_MAX` or simply run past EOF** and the section is
   still read (`loader.c:448`). The corpus case `sh_offset_size_overflow`
   (`tests/host/elf_corpus.c:218`) sets `sh_offset = 0xffffff00`, `sh_size = 0x200` and has been an
   expected failure since LEG-26, marked `UNPLANNED:LEG-34/BL-ELF-EXTENT` precisely because it
   cannot turn green without the API change this spec makes.

This is the finding that made the fuzz job red once `continue-on-error` was removed. The flag went
away on the strength of a single green run — a poor inference about a stochastic tool — and the very
next runs found this. Closing the finding is the right way to make CI green again; re-adding the
flag would only hide it.

## Scope

Give the pure unit the notion of an image size, and use it.

1. `duneos_elf_io_t` gains the image size. Every construction site is updated: the kernel's
   `FILE *`-backed io in `loader.c`, and the memory-backed io in `tests/host/elf_corpus.c` and
   `fuzz_elf_validate.c`.
2. `duneos_elf_image_open()` rejects, before allocating anything, any section whose extent escapes
   the image: `sh_offset > size`, `sh_size > size - sh_offset` (the form that cannot itself
   overflow), with a named reject reason.
3. The shstrtab allocation is bounded by construction, so the 2.4 GiB request becomes an early
   rejection rather than a `malloc` that happens to fail.

## Acceptance criteria

1. The corpus case `sh_offset_size_overflow` **turns green** and its `UNPLANNED:LEG-34` marker is
   deleted, with `ELF_CORPUS_EXPECTED_XFAILS` updated in the same change — the mechanism LEG-26
   built makes an unremoved marker fail the suite, so this is not optional.
2. The rejection is asserted on the **return code and the reject reason**, not on the absence of a
   crash. A new `duneos_elf_reject_t` enumerator names the offending field.
3. `sh_offset > size` and `sh_size > size - sh_offset` are both exercised, and so is the wrapping
   form the corpus already carries. A check written as `sh_offset + sh_size > size` would itself
   overflow and is explicitly wrong here.
4. A **valid** ELF is still accepted, and every one of the 57 real `app.elf` objects built under
   `apps/**/build/` still opens — the LEG-01 verification established that as the regression bar for
   this unit, and a bound that is too tight rejects a legitimate toolchain output. (They are build
   artefacts, not tracked files: `git ls-files | grep app.elf` returns nothing, and the bar is
   whatever a full `dbt buildall` has produced in the working tree.)
5. `make -C tests/host test` exits 0 with the full corpus; only `symtab_size_zero` (LEG-35) remains
   an expected failure, and the UNPLANNED count line reflects that.
6. `make -C tests/host fuzz-run` completes with no OOM and no crash on the seeded corpus. The
   allocation the fuzzer found must no longer be reachable.
7. The CI `fuzz-elf` job passes on this branch, and `continue-on-error` stays absent.
8. Both QEMU boards still exit 0 with every assertion matched: the nominal load path is unchanged.
9. `DUNEOS_ABI_VERSION` is bumped if and only if the change crosses the app boundary. It should not
   — `duneos_elf_io_t` is loader-internal — but the answer is stated either way.

## Out of scope

LEG-35 (`symtab_size_zero`, a zero-size symbol table) — a sibling finding that needs no image size
and stays an expected failure here; bounding `sh_link`/`sh_name`/`st_name` (LEG-01/02/03, shipped);
the relocation engine; changing how the loader reads sections once they are validated.

## Risks

The bound is on the path every application load takes. Criterion 4 is the guard against the obvious
failure: a check that is one byte too strict rejects real objects, and the symptom would be "no app
loads any more" — worse than the vector being closed. The 57-object corpus is cheap to re-run and is
the only evidence that matters here.

`sh_offset + sh_size > size` is the natural way to write the check and it is wrong: the sum is
computed in `uint32` and wraps, which is the exact defect being fixed. Criterion 3 exists because a
fix written from the shape of the bug rather than from the arithmetic would reintroduce it.

## Open questions

None

## Verification addenda (adversarial review of the first implementation)

The first implementation exempted SHT_NOBITS and SHT_NULL from the extent check entirely. Two
consequences, both reachable from the same untrusted `.dap` and both closed here rather than
deferred, because `elf_parse.h` now advertises an extent guarantee callers size allocations from:

1. **`sh_link` type.** `check_section_table()` bounded `sh_link` as an index but not the type of
   the section it names, and `loader.c` sizes the symbol string table `malloc()` from
   `shdrs[symtab->sh_link].sh_size`. A `strtab` typed `SHT_NOBITS` with `sh_size = 0x94000001` was
   accepted (`rc=0`, `why=DUNEOS_ELF_REJ_NONE`) and produced the fuzzer's 2.4 GiB request through a
   different field. An `SHT_SYMTAB`'s `sh_link` must now name an `SHT_STRTAB`
   (`DUNEOS_ELF_REJ_SH_LINK_TYPE`), asserted by corpus case `symtab_strtab_nobits`, and the guard is
   duplicated at the indexing site in `loader.c` per the convention LEG-02 set.
2. **`load_sections()` wrap.** An `SHF_ALLOC` NOBITS `.bss` with `sh_size = 0xfffffffe` passes any
   image-extent check by design, and `(sh->sh_size + 3u) & ~3u` is `size_t` arithmetic — 32-bit on
   ESP32-S3 — so it wraps to 0: the section adds nothing to the pool total, then pass 2 memsets
   4 GiB into a zero-sized placement. Not an extent question (a genuine `.bss` size is not bounded
   by the file), so the fix is a per-section cap against the memory that must hold it, applied
   **before** the alignment arithmetic, which itself wraps.

**The exemption that remains, and why.** Only `sh_size`, only for `SHT_NOBITS`, and never for
`e_shstrndx`. `sh_offset` is now bounded for every section without exception. Dropping the
`sh_size` exemption too would pass today's 57 objects built under `apps/**/build/` (largest NOBITS
`sh_offset + sh_size` is 29 876, inside every file), but it bounds a **memory** size by a **file** size: the first app
with a `.bss` larger than its own binary — a static framebuffer on a board with PSRAM — would stop
loading, and the symptom would be a rejection naming the wrong cause. Corpus case
`nobits_size_past_eof` pins that acceptance so removing the exemption fails loudly instead.

`SHT_NULL` was exempted alongside `SHT_NOBITS` in the first implementation and is not any more (PR
review, round 1). The reasoning that covers `SHT_NOBITS` does not transfer: a NOBITS `sh_size` is a
memory size the file genuinely cannot bound, whereas a `SHT_NULL` `sh_size` is meaningless and zero
in every well-formed object — so the exemption could never rescue a legitimate image and only
carried an attacker-controlled `uint32` through the guarantee `elf_parse.h` advertises.
`load_sections()` then acts on it: `duneos_elf_classify_section()` dispatches on the name and
`sh_flags`, never on `sh_type`, and pass 2's zero-fill branch tests `SHT_NOBITS` alone — so a
`SHT_NULL` section named `.data` with `sh_size = 0x100000` in a 400-byte object was placed in a
1 MiB data pool and then *read from the file*. The short read fails the load cleanly, so it was an
attacker-controlled allocation rather than corruption; it is exactly the class this spec exists to
remove.

## Verification addenda (second round)

**The section-total accumulator.** The per-section ceiling above removes the round-up wrap of a
single section; the **sum** was still unguarded. With `CONFIG_SPIRAM` the ceiling is 4 MiB and
`DUNEOS_LOADER_MAX_SECTIONS` is 1024, so 1024 allocatable sections of exactly 4 MiB sum to 2^32 and
wrap a 32-bit `size_t` to 0: no pool is allocated and pass 2 then memsets or reads through a NULL
`data_pool`. Narrow — PSRAM boards only, a crafted `.dap` of at least 4 MiB at the maximum section
count, and only that exact total (any other over-large sum fails cleanly at the `malloc` with
`-ENOMEM`); on the CardPuter the ceiling is 1 MiB, so the maximum total is 1 GiB and cannot wrap at
all. It is nonetheless the same arithmetic class this spec is about, so both accumulators —
`data_total` and `exec_total`, which have the same shape — now reject with `ESP_ERR_INVALID_ARG`
when `total > SIZE_MAX - rounded`, before adding.

It is not reachable from the host corpus: `load_sections()` is in the target-only half of the
loader and the input is a multi-megabyte object at the section-count limit. The arithmetic is pinned
instead by `test_section_total_arithmetic()` in `tests/host/test_elf_validate.c`, the way
`test_extent_arithmetic()` pins the extent check — the unguarded sum asserted to wrap to 0, then the
guard asserted to reject at the last section.

**The `.text` ceiling no longer comes from the exec pool.** Sizing it from `s_exec_pool_size` made
this ceiling, rather than the pool check at the allocation site, the thing that reported an
oversized `.text`: with `ESP_ERR_INVALID_ARG` where `ESP_ERR_NO_MEM` is the truthful code, and —
when the exec pool failed to allocate at boot, so `s_exec_pool_size == 0` — with
`section '.text': sh_size=N B over the 0 B ceiling` on **every** app, naming the wrong cause for a
pool that simply is not there. One ceiling (`LOADER_MAX_SECTION_BYTES`) now applies to every kind.
It is far above any exec pool this board allocates, so a `.text` too large for the pool falls
through to the pool check and is reported as `exec pool full` / `-ENOMEM` exactly as before the
cap existed; the ceiling only has to be low enough to make the round-up safe. Skipping the cap when
`s_exec_pool_size == 0` would have fixed the boot-failure case alone and left the wrong return code
in the ordinary one.

## Criteria not closed on this branch

Criteria 6 and 7 remain **unproven here** and must not be read as satisfied by this verification.

- **Criterion 6** — `make -C tests/host fuzz-run` did not run: no `clang` is installed on this
  machine, and the Makefile's fuzz target correctly refuses rather than silently fuzzing nothing.
- **Criterion 7** — the CI `fuzz-elf` job, the reason this cycle exists, has not executed here.

The evidence that stands in for them is a **surrogate**: the unmodified fuzz entry point built under
ASan+UBSan with `-max_allocation_size_mb=64`, 22 seeds and 200 000 mutations, clean; the same
harness against the pre-fix parser aborts with `allocation-size-too-big` at `elf_parse.c:115`. That
shows the harness has teeth and that the allocation is closed. It is not a libFuzzer run. The real
`fuzz-elf` job must still be observed green in CI before criteria 6 and 7 are closed.
