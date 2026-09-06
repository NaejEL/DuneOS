Status: APPROVED

# SPEC-leg-05 — Split the remainder of `duneos_loader_load()` after parser extraction

## Context

Finding LEG-05 (minor, M). **Moved from milestone 1 to milestone 0** at the 2026-09-03 arbitration,
and refocused: the split is no longer a standalone readability improvement, it serves the host
compilability of the parser.

`kernel/duneos_loader/src/loader.c:1231`, `duneos_loader_load()` spans **271 lines** (1231-1502) and
chains about ten steps (header validation, section table read, string table, classification, allocation, section
content read, symbol table, relocations, resolution, finalisation). Six distinct resources are
released through a single `out` label, each under a different condition.

Correction from adversarial validation: the initial claim that this was the only function above 200
lines is **false** — `supervisor_task` reaches about 213 lines. The finding stands on substance
(density of resources and error paths), but its title must not claim an inaccurate exclusivity.

### Status of the dependencies, 2026-09-06

LEG-25, LEG-26 and LEG-27 are all **DONE**. Criteria 5, 6 and 7 are therefore live requirements,
not the conditional ones the original text hedged ("if already in place"). The safety net the Risks
section hoped for exists: 23 corpus cases, two QEMU boards, and a libFuzzer job in CI.

### The risk this spec did not carry: stack depth (LEG-37)

**This is the same operation, on the same code path, that bricked a physical CardPuter.** LEG-37:
an ELF-parser extraction whose commit message said "extraction at constant behaviour" added one
struct local and one call frame on a path reaching `lfs_bd_crc`; the 3584 B ESP-IDF default
`main_task` stack had nothing left to absorb it and it overflowed into the adjacent heap. The
symptom was TLSF free-list corruption and a watchdog reboot loop surfacing thousands of instructions
later, never a stack-overflow report.

`duneos_loader_load()` runs on `main_task` during boot (the init scan) and splitting it into named
steps adds call frames to exactly that path. On Xtensa each frame also spills 16-64 B of windowed
registers.

What changed since LEG-37, and why this is now attemptable rather than reckless: the CardPuter
declares a **measured** 4608 B (`boards/m5stack-cardputer/board.yaml:38`; peak 3764 B, 784 B usable
margin after the watchpoint's 60 B), and `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` is armed
project-wide (`sdkconfig.defaults:57`) so the next overflow names itself in a single run.

Neither host tests nor QEMU can catch this: the host corpus runs with a host-sized stack, and the
QEMU bench is bounded by ADR 039 to "the loader, the VFS and the boot sequence, not the CardPuter".
Only the board can. Hence criteria 8 and 9.

### Relationship with SPEC-leg-25

The overlap with LEG-25 is **partial, not total** — hence two separate specs rather than a merge:

- **LEG-25 extracts** the parsing and validation steps into a pure host-compilable unit. That is a
  subset of the ten steps, the one depending on libc only.
- **LEG-05 (this spec) handles the remainder**: allocation through `heap_caps_*`, section content
  reading, relocation application, symbol resolution against the kernel table, finalisation. Those
  steps are **not** host-extractable — they depend on the IDF heap, the target architecture and the
  supervisor — and will remain in `loader.c` after LEG-25.

The ordering is therefore constrained: LEG-25 first, LEG-05 afterwards on what remains. Running
LEG-05 before LEG-25 would produce a split that has to be redone.

## Scope

After the extraction performed by LEG-25, extract the remaining steps of `duneos_loader_load()` into
named static functions, at strictly identical behaviour, preserving the resource release discipline.

## Acceptance criteria

1. `duneos_loader_load()` is under 100 lines and its body reads as a sequence of named step calls,
   including the call into the pure unit extracted by LEG-25.
2. Each extracted step is a `static` function, carries a name describing its role, and returns an
   error code following the repository convention (`int`, 0 on success, `-errno` on failure).
3. No resource is leaked on any error path: for every possible exit point, every allocation already
   made is released exactly once. The release discipline is verifiable by reading a single function,
   without having to follow six distinct conditions.
4. Observable behaviour is unchanged: loading, running and unloading a valid `.dap` yields the same
   result as before the split, and rejected files are rejected for the same reasons with the same
   `klog_e` messages.
5. The LEG-26 host suite passes with exactly the same outcome before and after the split: 23 corpus
   cases, no case changes verdict, `test_elf_validate` and `test_elf_scan` check counts unchanged.
6. The LEG-27 QEMU smoke test passes after the split, on **both** boards (`esp32s3-qemu` and
   `esp32s3-qemu-psram`).
7. The kernel builds for the `m5stack-cardputer` board with no new warning.
8. **The `main_task` high-water mark is re-measured on the physical CardPuter after the split, and
   reported as a number.** Not "it boots" — the measured peak and the resulting margin against the
   declared 4608 B. If the margin falls below the 784 B this branch starts from, that is a finding
   to report, not a value to silently accept: either the split is reshaped to nest less deeply, or
   `main_task_stack` is raised **with the new measurement written into the board.yaml comment**
   beside the existing derivation.
9. **The board is booted with the split kernel before this reaches `main`**, per the standing rule
   in CLAUDE.md ("A change that alters what a physical board links, or how deep it recurses, must be
   run on that board before it reaches `main`"). A green CI is explicitly NOT sufficient for this
   spec, and the report must say whether the board run happened rather than implying it.

## Out of scope

Extracting the ELF parser/validator (that is LEG-25); adding the missing bounds checks (that is
LEG-01/02/03); splitting `supervisor_task` (to be handled separately); changing the `.dap` format or
the ABI; removing FreeRTOS from the loader (Phase 26); optimising loading performance.

## Risks

Refactoring a critical path. The safety net depends on the execution order within milestone 0: if
LEG-26 and LEG-27 are already done, criteria 5 and 6 provide real verification; otherwise the only
protection is criterion 4, checked manually. **Running this spec after LEG-26 and LEG-27 rather than
before is therefore clearly preferable**, even though the hard dependency is only on LEG-25.

To be executed as an isolated change, never at the same time as a hardening, so that any regression
is attributable.

## Open questions

None. Two constraints are non-negotiable and are recorded as criteria rather than questions: the
hardware boot (criterion 9) and the re-measured margin (criterion 8).
