Status: APPROVED

# SPEC-leg-28 — Guard each arch HAL source on the symbol that already guards its consumer

## Context

Blocker 1 of SPEC-leg-27's QEMU bench, found on the first real emulator run (2026-09-03,
`esp32s3-qemu`) and confirmed with GDB against the running machine. It is the reason the
`qemu-smoke` CI job is red today, on both boards.

`arch/xtensa_esp32s3/arch.cmake` links the ADC into **every** ESP32-S3 board unconditionally:
line 35 appends `hal/hal_adc.c` to `DUNEOS_KERNEL_SRCS`, line 55 appends `esp_adc` to
`DUNEOS_KERNEL_REQUIRES`. Neither is conditional on the board declaring an `adc:` or `battery:`
section, so a board that has no analog peripheral at all still drags the component in.

`esp_adc` then registers `adc_hw_calibration()` as a GCC constructor
(`components/esp_adc/adc_common.c:74`). Constructors run from `__libc_init_array()` inside
`start_cpu0_default()` (`components/esp_system/startup.c:183`) — **before `app_main`**, before
`main_task` even. On real silicon it returns in microseconds. Under `qemu-xtensa`
(`esp_develop_9.2.2_20250817`) it reaches `read_cal_channel()` (`adc_hal_common.c:126`), which
busy-waits on `SENS.sar_meas1_ctrl2.meas1_done_sar`
(`components/esp_hal_ana_conv/esp32s3/include/hal/adc_ll.h:1085`) — a status bit the emulator
never sets. Boot stops there: no panic, no reboot, no watchdog, one core spinning at 100%
forever. Nothing of DuneOS runs, so the QEMU bench cannot assert anything at all.

Verified by removal: with both `arch.cmake` lines deleted as a throwaway experiment, the same
image boots through to `main_task: Calling app_main()`, mounts `/flash`, scans `/flash/bin`,
finds `qemu_smoke` and launches it.

This is not only a QEMU concern. Linking a peripheral driver — and running its constructor —
into boards that declared no such peripheral contradicts the driver-selection model the
project already uses everywhere else (`CONFIG_DUNEOS_DRV_*` emitted by
`tools/duneos-bspgen.py` from a `board.yaml` section, see CLAUDE.md "Kernel driver selection
model"). The ADC is the one peripheral that escaped it, because it sits in `arch.cmake` rather
than in `kernel/duneos_kernel/CMakeLists.txt`.

## Scope

Bring the ADC under the existing declarative driver-selection model, using **only symbols that
already exist and are already emitted**:

- `arch/xtensa_esp32s3/arch.cmake` guards its `list(APPEND DUNEOS_KERNEL_SRCS …)` entry for
  `hal/hal_adc.c` on `CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE` — the symbol that already gates
  `hal_adc.c`'s sole in-tree consumer, `drv_battery_adc_simple.c`, and which
  `tools/duneos-bspgen.py` already emits from `battery: { type: adc_simple }`. The same treatment
  applies to the other unguarded peripheral HAL sources (`hal_i2c.c`, `hal_spi.c`,
  `hal_encoder.c`), each on the symbol that already gates its consumer, and to
  `arch/xtensa_esp32/arch.cmake`'s `DUNEOS_ARCH`-gated HAL block.
- **No bspgen change, no `board.yaml` schema change, no new `CONFIG_*` symbol.** The four guards
  are pure CMake edits.
- **`DUNEOS_KERNEL_REQUIRES` is not guarded, and must not be.** `esp_adc` stays in the REQUIRES
  list. `CONFIG_*` variables are empty during the ESP-IDF requirements phase (CLAUDE.md,
  `arch.cmake` guard pattern), so a `CONFIG_*` guard on a REQUIRES entry would make the component's
  headers vanish at compile time on the boards that do need it. Dropping the source is what stops
  the component being linked and its constructor being run; the REQUIRES entry is include-path
  cost only. See the Risks section — this is the one way this fix can be got wrong.
- The QEMU boards (`boards/esp32s3-qemu`, `boards/esp32s3-qemu-psram`), which declare no analog
  peripheral, therefore no longer compile `hal_adc.c`, no longer link `esp_adc`'s objects and no
  longer run its constructor.
- Existing ADC boards keep exactly the ADC they have today.

## Acceptance criteria

1. A board whose `board.yaml` declares no ADC-backed `battery:` compiles no `hal_adc.c`, and the
   symbol `adc_hw_calibration` is **absent from the build's `duneos.map`**. Verified by grepping
   the link map. `esp_adc` may legitimately REMAIN in `DUNEOS_KERNEL_REQUIRES`: an unreferenced
   archive member is not linked and its constructor does not run, and `CONFIG_*` is empty during
   the ESP-IDF requirements phase so guarding a REQUIRES entry on such a symbol is not even legal
   (CLAUDE.md, `arch.cmake` guard pattern). Removing the source is the fix; the REQUIRES entry is
   cosmetic.
2. `boards/m5stack-cardputer` (ADC battery) and any other board declaring an ADC keep
   `hal_adc.c` and `esp_adc`, and their generated files are unchanged — no symbol is added or
   removed, since no bspgen change is involved. `idf.py build` for the CardPuter passes with no
   new warning.
3. `python tools/dbt.py qemu --board esp32s3-qemu` boots past the constructor: the boot-progress
   marker `app_main entered (IDF)` is reported `[ok]`. It may still fail on SPEC-leg-29's
   blocker; it must no longer hang before `app_main`.
4. No bspgen change is required for any of the four sources: every guard symbol already exists
   and is already emitted from the right `board.yaml` section. Verification is therefore at the
   CMake/link level (the map-file check of criterion 1, plus object-file presence), run for one
   declaring and one non-declaring board.
5. No generated file (`board_config.h`, `sdkconfig.board`, `partitions.csv`,
   `idf_target.txt`) is hand-edited; every change goes through the YAML or bspgen.
6. The `qemu-smoke` CI job's dependence on this spec is removed from
   `.github/workflows/ci.yml` once the job's remaining blocker is also resolved.
7. `hal_i2c.c` is compiled only when `CONFIG_DUNEOS_DRV_I2C=y`. Verified: `boards/esp32s3-qemu`
   (no `i2c:`) produces no `hal_i2c.c.obj`; `boards/m5stack-cardputer` still does.
8. `hal_spi.c` is compiled only when `CONFIG_DUNEOS_DRV_SPI=y`. Same verification, same boards.
9. `hal_encoder.c` is compiled only when `CONFIG_DUNEOS_DRV_INPUT_ENCODER=y`. Verified:
   `boards/lilygo-t-embed-cc1101` (declares `encoder:`) still compiles it and still links
   `esp_driver_pcnt`; `boards/m5stack-cardputer` and both QEMU boards do not.
10. `arch/xtensa_esp32/arch.cmake`'s `DUNEOS_ARCH`-gated HAL block (lines 26-48) receives the
   identical guards, so the non-IDF ESP32 path cannot re-introduce the same over-link. Verified by
   inspection; no build exercises this path today.
11. Every existing board still builds (`dbt flash kernel --build-only` for `m5stack-cardputer`,
   `esp32s3-devkitc`, `lilygo-t-embed-cc1101`) with no new warning, after a fullclean.

## Out of scope

- The QEMU IRAM/DRAM alias blocker — SPEC-leg-29.
- Making `esp_adc`'s constructor QEMU-safe, or patching ESP-IDF in any way. The fix is not to
  link the component at all for boards that do not use it.
- The REQUIRES-level over-declaration: `driver`, `esp_eth`, `esp_netif`, `esp_driver_sdspi` in
  `arch.cmake:45-59`, and `fatfs` / `esp_wifi` / `esp_netif` / `esp_event` / `nvs_flash` in
  `kernel/duneos_kernel/CMakeLists.txt:68,74-79`, and `esp_driver_usb_serial_jtag` in
  `main/CMakeLists.txt:4`. A DIFFERENT problem with a different risk profile: header-only cost, no
  reachable constructor, and not guardable on `CONFIG_DUNEOS_DRV_*` at all. Own spec, low priority.
- `CONFIG_DUNEOS_DRV_LOGIC`, which `tools/duneos-bspgen.py` emits unconditionally for EVERY board
  (confirmed: `boards/esp32s3-qemu/sdkconfig.board:23` has it on a board declaring nothing), while
  `kernel/duneos_kernel/Kconfig:31-33` declares it `default n` and no `board.yaml` in the repo has
  a `logic:` section. Same defect class, one layer up, but it is the only item needing a schema
  addition AND a product ruling: is `/dev/logic0` an always-available DuneOS feature like
  `/dev/null`, or a board-declared peripheral? Own spec.
- Any change to `hal_adc.c` itself.

## Risks

- **Guard SRCS, never REQUIRES.** `kernel/duneos_kernel/CMakeLists.txt:228` registers the component
  with `WHOLE_ARCHIVE`, so every `DUNEOS_KERNEL_SRCS` entry is force-linked whether referenced or
  not, and drags in the ESP-IDF objects it calls along with their `.init_array` constructors. That
  is why a SRCS entry is a behavioural defect and a REQUIRES entry is only include-path cost. All
  four changes must touch `list(APPEND DUNEOS_KERNEL_SRCS …)` only: `CONFIG_*` values ARE available
  in the normal build phase but are empty during requirements resolution, so the same guard on a
  REQUIRES entry would make headers vanish at compile time on boards that do need them.
- `esp_adc` may be pulled in transitively by another component in `DUNEOS_KERNEL_REQUIRES`; the
  constructor runs whenever the component is linked, regardless of who asked for it. Verify by
  the emulator run of criterion 3, not by reading CMake.
- Removing a component from `DUNEOS_KERNEL_REQUIRES` can break the ESP-IDF *requirements* phase
  rather than the compile phase, where `CONFIG_*` variables are empty (CLAUDE.md, `arch.cmake`
  guard pattern). The guard must be written against a signal that exists in that phase, or the
  headers go missing at compile time on ADC boards.
- No `sdkconfig.board` changes, but the guards move sources in and out of the component's source
  list, which CMake resolves only at configure time: every board needs a fullclean before its next
  build.

## Open questions

None. Both former questions are settled by the audit: no new symbol is needed — `hal_adc.c`'s sole
in-tree consumer is `drv_battery_adc_simple.c`, already gated on
`CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE`, which bspgen already emits exactly when
`battery: { type: adc_simple }` is declared. A standalone `adc:` section only becomes necessary if
a board wants an ADC without a battery, which no board does today.
