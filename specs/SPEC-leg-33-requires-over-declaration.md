Status: PROPOSED

# SPEC-leg-33 — REQUIRES over-declaration: two lines to delete, nine consumers to document

## Context

**A verification pass against current `main` demolished most of this spec's original premise.**
It was filed from the QEMU-bench audit (SPEC-leg-28/29/30) claiming nine over-declared
`DUNEOS_KERNEL_REQUIRES` entries. **Seven of the nine are genuinely REFERENCED** and must stay. The
evidence is recorded below file:line by file:line, so that a future audit reading the same lists
does not re-file them.

### Entries that were claimed over-declared and are NOT — each must stay

| Entry | Declared at | Referenced by |
|---|---|---|
| `esp_driver_sdspi` | `arch/xtensa_esp32s3/arch.cmake:72` | `kernel/duneos_kernel/src/vfs.c:244` `SDSPI_HOST_DEFAULT()`, `:263` `sdspi_device_config_t` / `SDSPI_DEVICE_CONFIG_DEFAULT()`, `:276` `esp_vfs_fat_sdspi_mount()` |
| `fatfs` | `kernel/duneos_kernel/CMakeLists.txt:67` | `kernel/duneos_kernel/src/vfs.c:4` `#include "esp_vfs_fat.h"` |
| `esp_driver_usb_serial_jtag` | `main/CMakeLists.txt:4` | `main/main.c:36` `usb_serial_jtag_driver_install()` (also `main.c` includes `driver/usb_serial_jtag.h` and `driver/usb_serial_jtag_vfs.h`) |
| `nvs_flash` | `kernel/duneos_kernel/CMakeLists.txt:75` | `kernel/duneos_kernel/src/drivers/net/drv_wifi.c:22-26` |
| `esp_wifi` | `kernel/duneos_kernel/CMakeLists.txt:76` | `drv_wifi.c:22-26` |
| `esp_netif` | `kernel/duneos_kernel/CMakeLists.txt:77` | `drv_wifi.c:22-26` |
| `esp_event` | `kernel/duneos_kernel/CMakeLists.txt:78` | `drv_wifi.c:22-26`, and `arch/xtensa_esp32/hal/hal_eth.c:24` `#include "esp_event.h"` |
| `esp_eth` | `arch/xtensa_esp32s3/arch.cmake:76` | `arch/xtensa_esp32/hal/hal_eth.c:19-22` (`esp_eth.h`, `esp_eth_mac.h`, `esp_eth_mac_esp.h`, `esp_eth_phy.h`), `arch/xtensa_esp32/hal/hal_phy.c:12-16` (`esp_eth_phy_lan87xx/ksz80xx/rtl8201/ip101.h`) |

`esp_eth`'s placement in the **S3** file is an admitted layering wart, already documented in place
at `arch/xtensa_esp32/arch.cmake:11-13`: `CONFIG_IDF_TARGET_ESP32` is unavailable during the
requirements phase, so the S3 file carries the Ethernet requirements for every Xtensa target and
the plain-ESP32 HAL borrows them. That is a design compromise with a written rationale, not
over-declaration.

`esp_hw_support` (`arch/xtensa_esp32s3/arch.cmake:67`) was already excluded from this spec's list;
re-verified as REFERENCED by `arch/xtensa_esp32s3/hal/hal_logic.c:17`
(`#include "esp_private/esp_clk.h"`), exactly as its in-place comment claims. Its fate depends only
on SPEC-leg-32's ruling.

### What actually remains

1. **`esp_netif` at `arch/xtensa_esp32s3/arch.cmake:77` is a pure duplicate.** The same component
   is declared unconditionally at `kernel/duneos_kernel/CMakeLists.txt:77`, in a list that applies
   to every board on every arch. Removing the `arch.cmake` copy changes nothing that the kernel
   list does not already provide. **This is the one safe deletion.**
2. **`driver` at `arch/xtensa_esp32s3/arch.cmake:66` is a build-verifiable candidate, not a
   verified one.** Every `driver/*.h` include in the tree is already covered by an explicitly
   listed `esp_driver_*` component:

   | Include | Site | Covered by |
   |---|---|---|
   | `driver/uart.h`, `driver/uart_vfs.h` | `arch/xtensa_esp32s3/hal/hal_uart.c` | `esp_driver_uart` (`arch.cmake:68`) |
   | `driver/gpio.h` | `arch/xtensa_esp32s3/hal/hal_gpio.c`, `kernel/duneos_kernel/src/vfs.c`, `kernel/duneos_kernel/src/drivers/display/st7789_hw.c` | `esp_driver_gpio` (`arch.cmake:69`) |
   | `driver/i2c_master.h` | `arch/xtensa_esp32s3/hal/hal_i2c.c` | `esp_driver_i2c` (`arch.cmake:70`) |
   | `driver/spi_master.h` | `arch/xtensa_esp32s3/hal/hal_spi.c`, `vfs.c`, `st7789_hw.c` | `esp_driver_spi` (`arch.cmake:71`) |
   | `driver/pulse_cnt.h` | `arch/xtensa_esp32s3/hal/hal_encoder.c` | `esp_driver_pcnt` (`arch.cmake:73`) |
   | `driver/usb_serial_jtag.h`, `driver/usb_serial_jtag_vfs.h` | `main/main.c` | `esp_driver_usb_serial_jtag` (`main/CMakeLists.txt:4`) |

   The umbrella `driver` component may nonetheless carry a transitive dependency nobody names in
   the tree. **Delete it only if a build proves it** — criteria 3, 4 and 5 are that proof.

**So the deliverable is roughly two lines of deletion, plus one in-place comment per surviving
entry naming its consumer by file:line. The bulk of this spec's work is documenting why entries
stay.** That is not a consolation prize: the reason this spec was mis-filed in the first place is
that eight of these entries carried no consumer reference, and the ninth's comment was doubted
rather than trusted. Comments that name a file:line are what stop the next audit from re-opening
this.

### The constraint that makes this different from SPEC-leg-28

**These entries CANNOT be guarded on `CONFIG_DUNEOS_DRV_*`.** `CONFIG_*` variables are empty during
the ESP-IDF requirements phase — stated in-tree at `kernel/duneos_kernel/CMakeLists.txt:47-51` and
in CLAUDE.md ("arch.cmake guard pattern"). A `CONFIG_*` guard on a REQUIRES entry makes the
component's headers vanish at **compile** time on the boards that DO need it — a failure that looks
nothing like its cause. Either an entry is removed outright, or it stays; there is no conditional
middle ground, and writing one is the single way this change can be got wrong.

### Why the risk profile differs from SPEC-leg-28's

`kernel/duneos_kernel/CMakeLists.txt:209` registers the kernel component with `WHOLE_ARCHIVE`
(`CMakeLists.txt:228`). Therefore:

- A **SRCS** entry is force-linked whether referenced or not, dragging in the ESP-IDF objects it
  calls and their `.init_array` constructors. That is why SPEC-leg-28 was a boot hang: behavioural.
- An **unreferenced REQUIRES** archive member is never linked and its constructor never runs. That
  is why this spec is cosmetic: include-path and build-graph cost only, no binary difference.

This asymmetry is the reason SPEC-leg-28 was urgent and this is not, and it must survive in the
spec so the next reader does not treat the two as the same class of defect.

### A polarity inconsistency worth ruling on while in there

```python
tools/duneos-bspgen.py:798    if board.get("wifi", True):
tools/duneos-bspgen.py:799        lines += ["CONFIG_DUNEOS_DRV_WIFI=y", ""]
```

WiFi is **opt-out** — the default is `True`, so a board gets `CONFIG_DUNEOS_DRV_WIFI=y` unless it
says otherwise. Every other capability in the generator is **opt-in**: `i2c`
(`tools/duneos-bspgen.py:717`), `spi` raw buses (`:720-722`), `battery` (`:724-725`), and so on all
test presence. WiFi is the only section in the whole generator with the inverted polarity.

This is **not a bug** — an ESP32-S3 has a radio whether or not the board file mentions it, so the
default is defensible. It is a trap: a future board author reading five opt-in rules will not
expect the sixth to be opt-out, and `wifi: false` is easy to forget on a board that should not
bring the radio up. It wants a documented decision, not a silent one. It stays in this spec, with
the other build-generator hygiene, rather than migrating elsewhere.

## Scope

- Delete `esp_netif` from `arch/xtensa_esp32s3/arch.cmake:77`, the verified duplicate of
  `kernel/duneos_kernel/CMakeLists.txt:77`.
- Attempt the deletion of `driver` from `arch/xtensa_esp32s3/arch.cmake:66`, and keep it deleted
  **only** if criteria 3, 4 and 5 all hold. If any of them fails, the line goes back with a comment
  recording what pulled it in — a negative result documented is a successful outcome here.
- Add, to every REQUIRES entry that stays, an in-place comment naming its consumer by file:line,
  using the table above. Entries that already carry a Phase-26/27 comment gain the consumer
  reference alongside it; nothing that explains a removal phase is deleted.
- Rule on the WiFi polarity and record the ruling: either document the opt-out explicitly in the
  schema comment block at the head of `tools/duneos-bspgen.py` and in CLAUDE.md, or flip it to
  opt-in and add `wifi:` to every board that needs it. Flipping is the larger change and touches
  every board file; documenting is the smaller and may well be right.

## Acceptance criteria

1. Every component removed from a REQUIRES list is demonstrated unreferenced first: no source
   compiled for that component's boards includes a header it provides. The evidence — the grep, per
   entry — is part of the change's record. The table in Context is a starting point, **re-run, not
   trusted**: it was itself the correction of a wrong list.
2. **No REQUIRES entry is made conditional on any `CONFIG_DUNEOS_DRV_*` symbol.** Entries are
   removed or kept, never guarded. Verified by inspection of the diff; a single `if(CONFIG_…)`
   around a `DUNEOS_KERNEL_REQUIRES` append fails this criterion outright.
3. All five boards build with **no new warning** after a fullclean: `m5stack-cardputer`,
   `esp32s3-devkitc`, `lilygo-t-embed-cc1101`, `esp32s3-qemu`, `esp32s3-qemu-psram`. Each into its
   own build directory with `-D SDKCONFIG=<build-dir>/sdkconfig`; never into `build/`.
4. **A removal must not change the link.** For `m5stack-cardputer`, the sorted list of symbols
   extracted from `duneos.map` is **byte-identical before and after** the change. A removal that
   changes the link means the component was referenced: the entry goes back, and the reason is
   recorded next to it. This is the gate on the `driver` umbrella specifically.
5. `python tools/dbt.py qemu --board esp32s3-qemu` and `--board esp32s3-qemu-psram` both still exit
   **0** with all five assertions matched.
6. Every REQUIRES entry that survives in `arch/xtensa_esp32s3/arch.cmake`,
   `kernel/duneos_kernel/CMakeLists.txt` and `main/CMakeLists.txt` carries a comment naming at
   least one consumer by `file:line`. An entry with no consumer named and no deletion is a failure
   of this criterion — it is exactly the state that produced this spec's wrong premise.
7. The WiFi polarity ruling is written down in `tools/duneos-bspgen.py`'s schema comment block and,
   if it is kept as opt-out, in CLAUDE.md's driver-selection section. If it is flipped, every board
   that needs WiFi declares it and criterion 3's boards all still enable `CONFIG_DUNEOS_DRV_WIFI=y`
   exactly as they do today.
8. `./tools/.dbt-venv/bin/python -m pytest tools/dbt/tests -q` passes. If the WiFi polarity is
   flipped, a test covers the new polarity; if it is documented, no test is required for it.
9. No generated file (`board_config.h`, `sdkconfig.board`, `partitions.csv`, `idf_target.txt`) is
   hand-edited.

## Out of scope

- Any `DUNEOS_KERNEL_SRCS` change. SPEC-leg-28 covered the behavioural half of this defect family
  and is closed; this spec touches REQUIRES only.
- `main/main.c:72`'s stale boot comment. **Moved to SPEC-leg-20**, the documentation-coherence
  batch, where it joins the wider sweep of stale `/flash/...` paths across `docs/`. It is not a
  build-system concern and the obvious correction turned out to be wrong — see SPEC-leg-20.
- The I2C guard / bus-0 mismatch — SPEC-leg-31.
- `CONFIG_DUNEOS_DRV_LOGIC`'s unconditional emission — SPEC-leg-32. `esp_hw_support`
  (`arch/xtensa_esp32s3/arch.cmake:67`) is genuinely referenced by `hal_logic.c:17` and is therefore
  **not** in this spec's removal list, whatever SPEC-leg-32 decides.
- Fixing the `esp_eth` / `esp_netif` layering wart itself — i.e. making `arch/xtensa_esp32/`
  declare its own requirements instead of borrowing the S3 file's. That is a bigger change and
  deserves its own spec; here the wart is only documented.
- The Phase 26/27 migrations the kept entries' comments point at (`fatfs`, `esp_driver_sdspi`,
  `esp_wifi` → `hal_net.h`). Those entries become removable when the migration happens, not before.
- Adding new components to any REQUIRES list.

## Risks

- **This spec was already wrong once.** Seven of nine claimed findings were false. The failure mode
  is not "a removal breaks the build" so much as "a plausible-looking list is trusted". Criterion 1
  exists because the list in Context is a corrected list, not a verified-forever one.
- **The failure mode of a wrong removal is delayed and misattributed.** A component removed from
  REQUIRES breaks the *requirements* phase or the compile phase on a board nobody rebuilds that
  week — `esp32s3-devkitc` and `lilygo-t-embed-cc1101` run on silicon and are rarely flashed.
  Criterion 3 builds all five boards for exactly this reason.
- **The `driver` umbrella is the one entry that can fail silently in the other direction.** Its
  transitive dependencies are not visible from any `#include` in the tree, which is why criterion 4
  compares the link and not the compile.
- **`CONFIG_*` is empty during the requirements phase.** Restated because it is the one way to get
  this wrong: a guard that looks correct in the editor makes headers disappear at compile time on
  the boards that need them. Criterion 2 is the gate.
- **Flipping the WiFi polarity is not cosmetic.** It changes `sdkconfig.board` on every board that
  omits the key, and a board that silently loses `CONFIG_DUNEOS_DRV_WIFI=y` fails only when
  something opens the radio. If it is flipped rather than documented, criterion 7's before/after
  comparison of the generated files is the gate.
- **Not an ABI change**: no exported symbol or ABI struct layout moves, so no `DUNEOS_ABI_VERSION`
  bump is expected.

## Open questions

1. ~~Is `esp_driver_sdspi` genuinely unreferenced? Same question for `fatfs`.~~ **Closed: both are
   REFERENCED and stay** — `vfs.c:244,263,276` and `vfs.c:4` respectively. See Context.
2. ~~Should the `esp_eth` / `esp_netif` layering wart be fixed rather than documented?~~ **Closed
   for this spec: documented.** `esp_eth` is referenced and stays; only the `esp_netif` duplicate
   goes. Fixing the wart properly is a separate, larger spec — recorded in Out of scope.
3. WiFi polarity: document the opt-out, or flip to opt-in? The trade-off is stated in Context; the
   ruling is the product owner's. **This is the only genuinely open question left.**
