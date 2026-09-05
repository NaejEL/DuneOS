Status: APPROVED

# SPEC-leg-01 — Bound every index and offset read from an ELF file

## Context

Findings LEG-01 (major, XS), LEG-02 (major, XS) and LEG-03 (major, S): three instances of the same
defect class in `kernel/duneos_loader/src/loader.c`. Fields read directly from the ELF file are used
as indices or offsets without ever being compared against the actual bounds.

- `elf_validate()` (l.233-270) only checks magic, class, endianness, `e_type`, `e_machine` and
  non-zero `e_shoff`/`e_shnum`. The only additional bound is `e_shnum > MAX_SECTIONS` (l.1140).
- LEG-01 — l.1158: `const elf32_shdr_t *ss = &shdrs[hdr.e_shstrndx];`. `e_shstrndx` is a `uint16`
  from the file, never compared to `e_shnum`. `malloc(ss->sh_size)` and
  `read_at(f, ss->sh_offset, ...)` then follow on arbitrary values. The same code is duplicated at
  l.1367 in `read_manifest_from_file()`.
- LEG-02 — l.1180: `const elf32_shdr_t *str_sh = &shdrs[shdrs[i].sh_link];`. `sh_link` is a `uint32`
  from the file, never bounded.
- LEG-03 — l.225 `return shstrtab + sh->sh_name;` and l.489
  `const char *name = strtab + sym->st_name;` followed by `strcmp`/`strncmp` on the result, without
  comparing the offset against the table size.

These three findings share a single root cause and the same test surface, hence a common spec.

The path is reachable without launching an application: `duneos_loader_scan()` calls
`read_manifest_from_file()` on **every file** encountered while scanning `/flash/bin/`, `/sd/bin/`
and `/sd/apps/`. The SD card is removable: its contents are untrusted input.

### Dependencies (milestone 0)

This spec runs **after milestone 0** and builds on it:

- **LEG-25** extracted the parser/validator into a pure host-compilable unit; the guards are added
  in that unit, not in the ESP-IDF-coupled body.
- **LEG-26** produced the malformed-ELF corpus and the host suite. **The acceptance criteria below
  are verified by running that suite, not by reading the code.** That is the whole point of
  milestone 0: with no hardware, a fix on this path cannot be validated any other way.

## Scope

Add bounds checking for section indices and string table offsets, so that a malformed ELF file is
rejected with an error code instead of causing a read outside the allocated buffers.

1. Check `e_shstrndx < e_shnum`.
2. Check `sh_link < e_shnum` before any indexing of `shdrs` by `sh_link`.
3. Make the string accessors carry the size of their table and check that the requested offset is
   strictly below that size before returning a pointer.
4. Ensure the fix also covers the `read_manifest_from_file()` path — moot if LEG-25 did remove the
   duplication, to be verified explicitly.

## Acceptance criteria

1. The LEG-26 corpus cases covering `e_shstrndx >= e_shnum`, `sh_link >= e_shnum`, out-of-table
   `sh_name` and out-of-table `st_name` **turn green**: each is rejected with the expected return
   code. The expected-failure markers placed by LEG-26 on those cases are removed, and their removal
   is part of this spec.
2. The corpus cases covering a **valid** ELF continue to be accepted: no hardening rejects a
   legitimate file.
3. `make -C tests/host test` returns a zero exit code, full corpus included.
4. The LEG-26 fuzzing target, run for the time bound defined by that spec, produces no crash and no
   out-of-bounds read on the ELF validation entry point.
5. The LEG-27 QEMU smoke test passes: `/flash` is mounted, `/flash/bin/` is scanned, and the
   embedded `.dap` application is loaded and executed as before the hardening — the fix does not
   break the nominal path.
6. `duneos_loader_scan()` applied to a directory containing the malformed corpus files terminates
   normally, skips those files, and still lists the valid applications present in the same
   directory.
7. Every rejection emits, from `loader.c`, a message identifying the offending field and its value,
   so that the reason for rejection is diagnosable from the klog ring.

## Out of scope

Rewriting the ELF validation strategy as a whole; cryptographic verification or signing of `.dap`
files; extracting the parser (that is LEG-25); splitting the rest of `duneos_loader_load()` (that is
LEG-05); creating the corpus and the fuzz target (that is LEG-26); hardening the relocation engine
or symbol resolution.

## Risks

Overly strict bounds could reject legitimate ELF files produced by certain toolchain versions:
criteria 2 and 5 explicitly require checking that a valid ELF and a real application still pass.

If milestone 0 has not been completed, this spec loses its verifiability: criteria 1 through 5 all
assume the corpus, the host suite and the QEMU smoke test exist. In that case, do not run it and
fall back on code review — that is exactly the situation the arbitration set out to avoid.

## Open questions

None
