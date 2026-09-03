Status: PROPOSED

# SPEC-leg-15 — Make `arch.cmake` discovery sensitive to changes

## Context

Finding LEG-15 (minor, XS): `kernel/duneos_kernel/CMakeLists.txt` line 91 discovers architectures by
glob, without `CONFIGURE_DEPENDS`:

```cmake
file(GLOB _DUNEOS_ARCH_MAKEFILES "${DUNEOS_ROOT}/arch/*/arch.cmake")
```

CMake evaluates that glob only at configure time. Adding or removing an `arch/<name>/arch.cmake`
therefore does not re-trigger configuration: an incremental build keeps using the old list and
diverges from a clean build, with no error message.

This is exactly the scenario the "Adding a new target architecture" procedure in `CLAUDE.md`
encourages — create an `arch.cmake` and nothing else — which makes the trap all the more likely.

## Scope

Make `arch.cmake` discovery sensitive to files being added and removed.

## Acceptance criteria

1. `arch.cmake` discovery re-triggers CMake configuration when a file matching the pattern is added
   or removed (through `CONFIGURE_DEPENDS`, or through an explicit architecture list that makes the
   glob unnecessary).
2. After a full build, adding an `arch/<name>/arch.cmake` file followed by an incremental build
   triggers reconfiguration: the new file is picked up without manual cleaning.
3. The same test with removal of an `arch.cmake` likewise triggers reconfiguration.
4. A kernel build for `m5stack-cardputer` produces the same set of arch sources as before the change:
   none added, none removed.
5. The three-condition guard pattern of each `arch.cmake` remains unchanged and functional:
   dependency resolution during the ESP-IDF requirements phase keeps populating
   `DUNEOS_KERNEL_REQUIRES`.

## Out of scope

Reworking the architecture selection mechanism; completing `arch/riscv32/` (LEG-24, not retained in
this cycle); applying the same treatment to any other globs in the project.

## Risks

`CONFIGURE_DEPENDS` makes CMake check the glob on every build, which has a measurable cost on large
trees; should it prove costly, the explicit list (allowed by criterion 1) is the alternative, at the
price of one line to add when creating an architecture — which would contradict the "zero changes to
this file" promise in `CLAUDE.md` and would then need documenting.

## Open questions

None
