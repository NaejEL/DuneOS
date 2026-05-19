# ADR 012 — Test strategy: host-side first, on-device smoke

**Status:** Accepted · 2026-05-19

## Context

DuneOS today has no test infrastructure. The `apps/test_exit/` and `apps/test_hardening/` apps are manual sanity checks — you flash them, watch klog, decide if it worked. The CI smoke test from [[ADR-007]] verifies that the kernel *builds* on three targets, not that it *runs correctly*.

Several upcoming refactors put this gap in the danger zone:

- **Phase 26 (OSAL)** rewrites the synchronisation primitives in `supervisor.c`, `task.c`, `klog.c`, `api.c`, and the VFS internal `.c`s. A subtle bug (lost queue message, wrong mutex order) won't surface until a specific timing window — months later, hard to bisect.
- **Phase 27 (native VFS)** replaces `esp_vfs_register` with a hand-rolled implementation supporting `poll`/`select`. The Linux VFS has 30 years of bug-fix history; reimplementing it without tests is reckless.
- **Phase 28 (RISC-V)** ports HAL backends. Without tests, "it boots" is the only validation — known-low bar.
- **Phase 29 (RP2040)** ports the kernel entirely to a new SDK. Identical concern, amplified.

A test framework needs three properties for DuneOS' constraints:

1. **Header-only or single-file vendored**, no `cmake`/`autoconf` ceremony. We don't want to import a build system to test a kernel.
2. **Works in both contexts**: target-side (linked into the kernel binary or a dedicated test app) and host-side (the Linux simulator in Phase 26).
3. **Tiny binary footprint** when target-side. We have 1.5 MiB factory partition; a test framework can't eat 200 KiB.

## Decision

DuneOS adopts **Greatest** (https://github.com/silentbicycle/greatest), a single-header C test framework (~700 lines), vendored as `third_party/greatest/greatest.h`. No build system dependency, no link-time runtime, ~5 KiB code overhead per test binary.

### Conventions

**File layout:**

```
kernel/duneos_kernel/tests/
    test_init_yaml.c          # tests for src/init.c
    test_supervisor.c         # tests for src/supervisor.c
    test_klog.c               # ...
kernel/duneos_loader/tests/
    test_loader_relocs.c      # tests for ET_REL relocation logic
libdune/tests/
    test_libdune_fs.c
    ...
```

One test file per module under test, named `test_<module>.c`. Each file is a self-contained `main()` invoking `GREATEST_MAIN_BEGIN/END` with its suites.

**Execution contexts:**

- **Host-side (preferred)** — built and run on Linux x86_64 via the simulator toolchain (Phase 26 deliverable). Uses real PicoLibc + pthread_osal. Tests run as a regular CLI binary. Fast (< 1 s per file), debuggable with gdb/valgrind.
- **Target-side (smoke / integration)** — built as a special `.dap` (`apps/test_<area>/`) deployed to a board, output to klog. Use only for tests that genuinely require real hardware (DMA timing, ISR latency, specific peripheral interaction). Most kernel logic tests do NOT need target-side runs.

**What gets tested:**

| Type | What | Where |
|---|---|---|
| **Unit** | Pure functions (parser, validator, allocator algorithm) | Host-side |
| **Module** | A module's API with its dependencies stubbed (mocked OSAL, mocked VFS) | Host-side |
| **Integration** | Interaction between modules with real OSAL (pthread sim) | Host-side |
| **Smoke** | "Kernel boots, loads `hello_world.dap`, sees `Hello\n` in klog" | Target-side, per board, in CI |
| **HW-specific** | "Encoder decodes A/B correctly with real GPIO" | Target-side, manual or hardware loop |

**What does NOT get tested:**

- Performance / latency. Profiling is a separate concern; no perf-regression suite in v1.
- Coverage measurement. Useful but adds tooling — defer until tests exist.
- Property-based / fuzz testing. Same.

### Implementation roadmap

- **Phase 26** — adds `third_party/greatest/` submodule + `dbt test` subcommand (`dbt test [--target=sim|board]`). Writes the first 5-10 unit tests for the OSAL primitives being introduced. Test coverage on the *new* code being written, not retroactive.
- **Phase 27** — VFS native rewrite writes tests as it goes (`test_vfs_mount`, `test_vfs_poll`).
- **Phase 28 / 29** — port engineers add host-side tests for any non-trivial logic they touch.
- **No big-bang back-fill.** Existing modules (`init.c`, `klog.c`, `supervisor.c`) get tests opportunistically — when a contributor touches them, they add the test that would have caught the bug they're fixing. This avoids the trap of spending a phase writing tests for stable code.

### CI integration

The smoke test from [[ADR-007]] gains a fourth step: after `dbt build` for the Linux simulator, run `dbt test --target=sim`. Build failure or test failure blocks PR merge. Target-side tests are NOT in CI v1 (require a board); they run manually before releases.

## Consequences

- Phase 26 is no longer "rewrite the kernel and hope" — every OSAL primitive gets its small test suite as it's written.
- Host-side debugging becomes the default: a failing test runs under gdb on a laptop in seconds. The cycle "build, flash, monitor, decide" only happens for hardware-bound bugs.
- `third_party/greatest/` adds one git submodule, no build complexity. The simulator phase 26 toolchain consumes it the same way `cjson`/`littlefs` are consumed today.
- A pattern emerges: *new code comes with tests, old code stays untested until touched.* This is sustainable for a solo-or-small project; "test everything before code" is not.
- No "test farm" infrastructure — boards stay on developers' desks. Smoke testing on hardware is manual before releases. Acceptable until the user base grows.
- Documentation: `docs/testing.md` lists the conventions and gives copy-paste examples. To be written alongside the first tests in Phase 26.

## Alternatives

- **Unity** (Throw The Switch) — rejected as primary. ESP-IDF bundles it, which is convenient on target but not portable to the simulator without effort. Greatest is simpler and works the same in both contexts.
- **Custom test macros** — rejected. Wastes time on tooling instead of tests. The Greatest source is 700 lines and would be readable if we ever need to fork it.
- **CMake-based test framework (e.g. CTest with cmocka)** — rejected. Drags in CMake-specific testing infrastructure that wouldn't help on the simulator pthread build.
- **Test on target only (no host-side)** — rejected. Per-test cycle of build + flash + watch klog is 30 seconds minimum; a 100-test suite takes ~50 minutes. Host-side: 1 second. The difference dictates whether tests get written at all.
- **No tests, document carefully** — rejected. Documentation rots; tests fail loud. Phase 26-29 are too large for code review alone.
