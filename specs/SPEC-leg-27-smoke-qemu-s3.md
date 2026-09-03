Status: PROPOSED

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

## Scope

Add a `dbt` subcommand that builds the image, launches QEMU on the ESP32-S3 target in
non-interactive mode, captures serial output, and returns a CI-usable verdict.

Project convention requires going through `dbt` rather than bare `idf.py`: the subcommand is added
to the parser in `tools/dbt/cli.py` alongside `flash`, `flashimg` and `system`.

## Acceptance criteria

1. `qemu-xtensa` is installable through a documented, automatable procedure
   (`idf_tools.py install qemu-xtensa`), and the documentation states the expected version
   (`esp_develop_9.2.2_20250817`).
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
   clean-exit marker emitted by the application itself proving its code actually ran.
7. The test application embedded in the image is an existing simple application (`hello_world` for
   instance) or a dedicated test application; its exit marker is specific enough not to be confused
   with another log message.
8. The command's exit code is 0 if and only if all assertions pass, and non-zero in every other case
   (failed assertion, timeout, system panic, QEMU missing). A panic or reboot of the system is
   detected and reported as a failure, not as silence.
9. The command works on a machine with no hardware attached: it reads neither `.duneos_port` nor any
   physical serial device.
10. A CI job runs this smoke test on `m5stack-cardputer` and fails if the exit code is non-zero.

## Out of scope

Emulating the SD card, display, keyboard or any other peripheral; peripheral mocks (deferred to the
Linux simulator of ADR 007, Phase 26); GDB debugging over port 3333; the RISC-V `esp32c3` target via
`qemu-riscv32` (conceivable later, outside this spec); performance testing; on-target code coverage;
replacing the host tests.

## Risks

QEMU ESP32-S3 does not faithfully emulate the whole SoC: some drivers declared in `init.yaml` may
fail to initialise under emulation without this being a code defect. Should that happen, the answer
is to use a reduced profile for the smoke test and document it — **not** to relax assertions until
they no longer prove anything.

Second risk: a test that merely looks for a string in the output can pass even though the system
panicked immediately afterwards. Criterion 8 explicitly requires detecting panic and reboot, failing
which silence would read as success — the costliest failure mode for a test bench.

## Open questions

None
