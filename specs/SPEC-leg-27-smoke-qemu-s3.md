Status: APPROVED

# SPEC-leg-27 — QEMU ESP32-S3 smoke test: boot and load a `.dap` from `/flash`

## Context

Enabler LEG-27 (milestone 0). Independent of LEG-25 and LEG-26: can be carried out in parallel.

The maintainer has no hardware on hand. Without a way to run `flash → scan → load .dap` end to end,
every change to the loader is validated blind.

The necessary pieces already exist:

- Espressif's `qemu-xtensa` supports `esp32` and `esp32s3` (ESP-IDF v6.0.1 `tools.json`, version
  `esp_develop_9.2.2_20250817`). The binary is not installed locally yet: obtain it with
  `idf_tools.py install qemu-xtensa`.
- `idf.py qemu` ships with IDF v6.0.1 (`tools/idf_py_actions/qemu_ext.py`): it generates
  `qemu_flash.bin` and `qemu_efuse.bin`, exposes serial on `socket:5555` and GDB on `3333`, and
  accepts extra QEMU arguments.
- DuneOS mounts `/flash` (LittleFS, `sysbin` partition) first and scans `/flash/bin/` before the SD
  card; `dbt flashimg` already builds that LittleFS image with embedded applications.

**The end-to-end `.dap` path is therefore reachable without emulating a single peripheral:** QEMU
boots the full flash image, and applications are read from `/flash/bin/`.

**Boundary not to be crossed:** this bench covers **boot and the loader via `/flash` only**. No
emulated SD card (SDMMC/SPI-SD support in QEMU S3 is unconfirmed), no display, keyboard or any other
peripheral mock. Peripheral mocks belong to the **Linux simulator of ADR 007 (Phase 26)**, not to
QEMU.

**Shape fixed by [ADR 039](../docs/adr/039-qemu-test-bench.md)** (Proposed, 2026-09-03): a QEMU
target is an ordinary board, not a mode of an existing one. Two boards are created,
`esp32s3-qemu` and `esp32s3-qemu-psram`, declaring only what QEMU models — UART, flash,
`has_sd: false`, no display and no keyboard. The pair exists because `loader.c` allocates app
sections under `#ifdef CONFIG_SPIRAM`, and only the DRAM branch is exercised today. PSRAM is
emulated (`ssi_psram`, quad by default, octal driven from `CONFIG_SPIRAM_MODE_OCT` —
`qemu_ext.py:305-307`), and bspgen already emits that symbol from a `psram:` section, so no new
glue is needed between `board.yaml` and the QEMU arguments.

## Scope

Create the two boards above, then add a `dbt` subcommand that builds the image, launches QEMU on
the ESP32-S3 target in non-interactive mode, captures serial output, and returns a CI-usable
verdict.

Project convention requires going through `dbt` rather than bare `idf.py`: the subcommand is added
to the parser in `tools/dbt/cli.py` alongside `flash`, `flashimg` and `system`. The emulator itself
is launched through **`plugin.run_qemu()`** on the toolchain plugin, beside the `build_kernel` /
`flash_kernel` / `monitor` methods introduced in Phase 21 — the shape Phase 28.5 already plans for
the extended plugin interface, so that the SDK decoupling does not have to redo it.

## Acceptance criteria

1. `qemu-xtensa` requires **no manual step whatsoever** on Linux. From a clean machine the entire
   sequence is `python tools/dbt.py qemu --board esp32s3-qemu` — no `source export.sh`, no PATH
   edit, no distro package, no `sudo`. `dbt` resolves the binary from the ESP-IDF tools tree (it is
   an `on_request` tool that `export.sh` does not put on PATH), installs it itself when absent, and
   passes `-nic none` so the emulator never loads `libslirp`. On Windows the command fails
   immediately and explicitly as unsupported, never as a PATH hunt. Documentation states the
   expected version (`esp_develop_9.2.2_20250817`).
2. A dedicated `dbt` subcommand (for example `python tools/dbt.py qemu`) exists, is registered in
   `tools/dbt/cli.py`, and is documented in its help output.
3. It builds the flash image through the existing `dbt flashimg` path, without duplicating its
   LittleFS image-building logic.
4. It launches QEMU for the `esp32s3` target in **non-interactive** mode: no terminal input is
   required, and the process terminates on its own.
5. A **timeout** is enforced: if the system does not reach the expected state within the allotted
   time, the command exits with a non-zero code and a message stating that this is a timeout,
   distinct from an assertion failure.
6. Serial output is captured and checked against klog assertions covering at minimum: successful
   mounting of `/flash`, the scan of `/flash/bin/`, loading of an embedded `.dap` application, and a
   marker emitted by the application itself proving its code actually ran. The payload is the
   observation channel: because `klog.c` forwards only level `E` to `ESP_LOG`, the boot lines reach
   the console only because the application drains `/dev/klog` to its stdout. That coupling is
   intended — an assertion set that could be satisfied without the loaded code running would not
   prove the loader.

6b. The bench must be able to distinguish a hung kernel from a healthy kernel that emits nothing the
   console can carry. Because `klog.c` forwards only level `E` to `ESP_LOG`, absence of output is
   not evidence of a hang. A diagnostic channel independent of the payload is required, and it must
   never be able to satisfy an assertion.
7. The test application embedded in the image is a dedicated test application; its markers are
   specific enough not to be confused with another log message, and its first instruction writes to
   a channel independent of where its `stdout` is routed, so that "the app never ran" and "the app
   ran but its output went nowhere" are distinguishable.

7b. The payload's `stdout` must reach the emulated console. A board declaring `console: uart` must
   not leave ESP-IDF's secondary console bound to a peripheral the emulator does not model.
8. The command's exit code is 0 if and only if all assertions pass, and non-zero in every other case
   (failed assertion, timeout, system panic, QEMU missing). A panic or reboot of the system is
   detected and reported as a failure, not as silence.
9. The command works on a machine with no hardware attached: it reads neither `.duneos_port` nor any
   physical serial device.
10. `boards/esp32s3-qemu/board.yaml` and `boards/esp32s3-qemu-psram/board.yaml` exist, declare no
    peripheral QEMU does not model, and run through `duneos-bspgen.py` producing the four generated
    files without a hand edit. The PSRAM board yields `CONFIG_SPIRAM=y` in its `sdkconfig.board`,
    the other `CONFIG_SPIRAM=n`.
11. The emulator is launched through `plugin.run_qemu()` on the toolchain plugin, not by a direct
    `idf.py qemu` call from `tools/dbt/`.
12. A CI job runs this smoke test on **both** QEMU boards and fails if either exit code is non-zero.
    Because switching `.duneos_board` requires a fullclean (`sdkconfig` is board-specific and CMake
    raises FATAL_ERROR on an unclean switch), each board builds in its own build directory or its
    own job.

## Emulator limits established by execution

Recorded on 2026-09-03 from real runs under `qemu-xtensa` `esp_develop_9.2.2_20250817`, diagnosed
with GDB against the live machine. Both are defects **outside this spec**, each to become its own:

1. **ESP-IDF's ADC calibration constructor never returns under QEMU.** `adc_hw_calibration`
   (`esp_adc/adc_common.c:74`, a GCC constructor) reaches `read_cal_channel`
   (`adc_hal_common.c:126`), which busy-waits on `SENS.sar_meas1_ctrl2.meas1_done_sar` — a bit the
   emulator never sets. This happens before `app_main`, so nothing of DuneOS runs. The DuneOS-side
   cause is that `esp_adc` is linked unconditionally for every board, including one declaring no
   ADC: `arch/xtensa_esp32s3/arch.cmake:35` and `:55`.

2. **QEMU does not alias the ESP32-S3 IRAM and DRAM views of internal SRAM.** Writing
   `0x3fca3454` leaves `0x40393454` unchanged and vice versa: two separate memories, not two views
   of one SRAM. The loader writes application text through the DRAM alias — the only way on real
   hardware — and executes at the IRAM address, so under emulation it executes zeroes and the
   application dies immediately with `cause=0` four words in. Espressif's own DIRAM aliasing tests
   carry the Unity tag `[heap][qemu-ignore]`, independently confirming the emulator is known-
   defective here. Patching the loader to write at the IRAM address instead was tested and makes
   the `.dap` execute correctly under QEMU, but is **incorrect on real silicon**: IRAM is 32-bit-
   access-only (`mem_alloc.rst:126`; `SOC_BYTE_ACCESSIBLE_*` covers the DRAM view only) while the
   loader performs unaligned byte stores into the instruction stream (`loader.c:520-540`) and reads
   sections straight into the exec block (`loader.c:429-430`). The fix belongs upstream in QEMU
   (`memory_region_init_alias()`), not in the loader.

Consequence: criterion 6 cannot go green until blockers 1 and 2 are resolved. Each now has its
own spec: blocker 1 is `SPEC-leg-28-adc-linked-unconditionally.md`, blocker 2 is
`SPEC-leg-29-qemu-iram-dram-alias.md`, both `PROPOSED`. Until both land the `qemu-smoke` CI job
carries `continue-on-error: true` with a comment naming them, so a permanently blocked job
neither gates the pipeline nor pretends to be healthy.
The bench itself is correct: with both patched out as a throwaway experiment, the loaded `.dap` ran
end to end — opens, five klog reads, writes and a clean `duneos_exit(0)`, confirmed under GDB.
What IS proven by execution: the kernel boots on a board declaring no display, SD, SPI,
I2C, battery or USB; `/flash` mounts; `duneos_loader_scan()` finds `/bin/qemu_smoke.dap` with
`has_sd: false`; and the supervisor launches it. Nothing in `duneos_vfs_init`, `init.c` or the
LittleFS mount blocks on a minimal board.

3. **A supervisor-launched application's `fd 1` is bound to the USB-Serial-JTAG VFS**, which QEMU
   does not model, so every `write(1, …)` returns -1 and no payload output reaches the console.
   `board.yaml` declares `console: uart` and bspgen emits `CONFIG_ESP_CONSOLE_UART_DEFAULT=y`, but
   ESP-IDF's S3 default `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y` is left in place. Kernel
   `ESP_LOGE` still arrives because it travels the `stdout` `FILE *` bound to `/dev/console`, not
   raw `fd 1` — which is what made this asymmetry so misleading. Unlike blockers 1 and 2 this one
   is IN SCOPE, and criterion 7b exists to close it.

   The mechanism is **not** the secondary console's write mirroring, which
   `console_write()` (`esp_stdio/stdio_vfs.c`, the compiled path since `CONFIG_VFS_SUPPORT_IO=y`)
   performs additively and which can never return -1. It is fd *allocation order*:
   `esp_vfs_open()` (`vfs/vfs_calls.c:32-56`) calls the filesystem's `open` callback before
   registering a global fd, so the first open of `/dev/console` recurses into `console_open()`,
   whose nested opens of `/dev/uart/0` and `/dev/secondary` claim global fds 0 and **1**, leaving
   `/dev/console` at fd 2. `write(1, …)` therefore reaches
   `usb_serial_jtag_write()` (`esp_driver_usb_serial_jtag/src/usb_serial_jtag_vfs.c:182-187`),
   which returns -1 outright when the USJ unit is not connected — always, under QEMU. With
   `CONFIG_ESP_CONSOLE_SECONDARY_NONE=y` the nested open disappears and fd 1 becomes
   `/dev/console`. Re-derived from ESP-IDF v6.0.1 sources on 2026-09-04 after the original
   GDB-based diagnosis was challenged; source and live observation agree. Full derivation in
   `docs/qemu-test-bench.md`, blocker 3.

## Out of scope

Emulating the SD card, display, keyboard or any other peripheral; peripheral mocks (deferred to the
Linux simulator of ADR 007, Phase 26); GDB debugging over port 3333; the RISC-V `esp32c3` target via
`qemu-riscv32` (conceivable later, outside this spec); performance testing; on-target code coverage;
replacing the host tests; fixing either emulator limit recorded above (each becomes its own spec);
modifying the loader to accommodate QEMU's missing SRAM alias.

## Risks

QEMU ESP32-S3 does not faithfully emulate the whole SoC: some drivers declared in `init.yaml` may
fail to initialise under emulation without this being a code defect. The dedicated boards are the
structural answer — they declare no such driver in the first place. Should a gap remain anyway, the
answer is to narrow the board or the profile and document it — **not** to relax assertions until
they no longer prove anything.

Bench scope is bounded and must be stated when reporting a green run: it proves the loader, the VFS
and the boot sequence, **not** the CardPuter. It says nothing about the shipping board's display,
keyboard or SD path. Closing that gap is step two of ADR 039 (a `qemu:` field on the real boards),
deliberately not committed to here.

Second risk: a test that merely looks for a string in the output can pass even though the system
panicked immediately afterwards. Criterion 8 explicitly requires detecting panic and reboot, failing
which silence would read as success — the costliest failure mode for a test bench.

## Open questions

None
