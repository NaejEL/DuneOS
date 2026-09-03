Status: PROPOSED

# SPEC-leg-25 — Extract the ELF parser/validator into a pure host-compilable unit

## Context

Enabler LEG-25 (milestone 0), hard prerequisite of LEG-26 and LEG-05, and indirect prerequisite of
the LEG-01/02/03 hardening.

`kernel/duneos_loader/src/loader.c` is **1720 lines** long and does not compile on the host. The
coupling runs deeper than the ESP-IDF dependencies alone:

- ESP-IDF / FreeRTOS: `freertos/FreeRTOS.h` (l.23), `freertos/task.h` (l.24), `freertos/semphr.h`
  (l.25), `esp_heap_caps.h` (l.18), `esp_rom_sys.h` (l.19), `soc/soc.h` (l.28);
- DuneOS kernel: `duneos/supervisor.h` (l.3), `duneos/api.h` (l.4), `duneos/klog.h` (l.16),
  `duneos/shellpipe.h` (l.17);
- third party: `cJSON.h` (l.20); plus `setjmp.h` (l.22) for the captured-app checkpoint.

In addition, `elf_validate()` returns `esp_err_t` and reports errors through `ESP_ERR_*`: the return
type itself is a link to ESP-IDF, whereas the repository error convention (ADR 001) is `int` / 0 /
`-errno`.

As long as this coupling holds, no host test can reach the validation logic, and the LEG-01/02/03
hardening could only be verified by code review.

## Scope

Extract from `loader.c` the **pure** ELF parsing and validation logic into a standalone compilation
unit, with no ESP-IDF, FreeRTOS or DuneOS kernel include, and whose access to file bytes goes
through an injected I/O layer.

Remaining in `loader.c`: allocation (`heap_caps_*`), relocation application, symbol resolution
against the kernel table, the `setjmp` checkpoint, cJSON manifest parsing, and all interaction with
the supervisor.

## Acceptance criteria

1. A dedicated compilation unit holds the ELF parsing and validation logic (at minimum
   `elf_validate()`, section header reading and classification, and the string table accessors) and
   includes **no** ESP-IDF, FreeRTOS, `soc/`, `cJSON.h` header, nor any DuneOS kernel header. It
   depends only on libc and its own headers.
2. Access to ELF file bytes goes through an injected abstraction (callback struct or equivalent):
   the unit references neither `FILE*` nor `open`/`read`/`lseek` directly, so that a test can drive
   it from an in-memory buffer.
3. This unit compiles on the host with `gcc -std=c17 -Wall -Wextra -Werror` (the options already in
   force in `tests/host/Makefile`), with no `-I` pointing at ESP-IDF.
4. The extracted functions return `int` (0 on success, `-errno` on failure) per the repository
   convention, and no longer reference `esp_err_t` or `ESP_ERR_*`.
5. Errors propagate by return value rather than through `klog_e` from inside the pure unit: logging
   stays in `loader.c`, which translates the returned code. No loss of diagnosability: for every
   distinct rejection reason, `loader.c` still emits a message identifying the offending field and
   its value.
6. `loader.c` consumes this unit and **no longer contains a copy** of the extracted logic: the
   current duplication between `duneos_loader_load()` (l.1158) and `read_manifest_from_file()`
   (l.1367) disappears, leaving a single implementation.
7. The kernel builds for the `m5stack-cardputer` board with no new warning, and observable behaviour
   is unchanged: loading, running and unloading a valid `.dap` yields the same result as before the
   extraction, and rejected files are rejected for the same reasons.
8. The extracted unit is compiled by both the kernel build and the host build: both consume the same
   source file, and no copy is made for testing.

## Out of scope

Adding the missing bounds checks (that is LEG-01/02/03, which comes afterwards and will build on
this extraction); extracting the relocation engine or symbol resolution; splitting the rest of
`duneos_loader_load()` (handled by LEG-05); writing tests and the corpus (handled by LEG-26);
removing FreeRTOS from the loader (Phase 26, OSAL); porting `klog` to the host.

## Risks

The extraction touches the loading path of every application, with no existing test net — which is
precisely the problem the milestone 0 sequence sets out to solve, and this spec is the one that runs
before the net exists. It must therefore be carried out at strictly constant behaviour
(criterion 7), fixing no bug along the way: mixing extraction and hardening would make any
regression unattributable. Bugs noticed along the way are recorded, not fixed here.

Second risk: an overly rich I/O abstraction would push into the pure unit responsibilities that
must stay in `loader.c`. Criterion 2 sets the necessary minimum; anything beyond it is a decision
to be documented.

## Open questions

None
