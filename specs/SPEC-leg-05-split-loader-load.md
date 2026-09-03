Status: PROPOSED

# SPEC-leg-05 — Split the remainder of `duneos_loader_load()` after parser extraction

## Context

Finding LEG-05 (minor, M). **Moved from milestone 1 to milestone 0** at the 2026-09-03 arbitration,
and refocused: the split is no longer a standalone readability improvement, it serves the host
compilability of the parser.

`kernel/duneos_loader/src/loader.c:1108`, `duneos_loader_load()` spans 229 lines and chains about
ten steps (header validation, section table read, string table, classification, allocation, section
content read, symbol table, relocations, resolution, finalisation). Six distinct resources are
released through a single `out` label, each under a different condition.

Correction from adversarial validation: the initial claim that this was the only function above 200
lines is **false** — `supervisor_task` reaches about 213 lines. The finding stands on substance
(density of resources and error paths), but its title must not claim an inaccurate exclusivity.

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
5. The LEG-26 host suite, if already in place, passes with exactly the same outcome before and after
   the split: no case changes verdict.
6. The LEG-27 QEMU smoke test, if already in place, passes after the split.
7. The kernel builds for the `m5stack-cardputer` board with no new warning.

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

None
