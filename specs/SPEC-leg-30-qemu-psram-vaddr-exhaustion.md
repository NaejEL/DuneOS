Status: APPROVED

# SPEC-leg-30 — `esp32s3-qemu-psram` exhausts the external-memory vaddr window and cannot find `sysbin`

## Context

With SPEC-leg-28 and SPEC-leg-29 landed, `python tools/dbt.py qemu --board esp32s3-qemu` exits
**0** with all five assertions matched. Its sibling `esp32s3-qemu-psram` still exits **2**: it
boots, reaches VFS init, fails to mount `/flash`, and DuneOS idles.

The root cause is established, not suspected.

`boards/esp32s3-qemu-psram/board.yaml` declares `psram_size_mb: 8`, but the generated
`boards/esp32s3-qemu-psram/sdkconfig.board` carries `CONFIG_SPIRAM_TYPE_AUTO=y`. ESP-IDF
therefore ignores the declared size and auto-detects whatever QEMU's `ssi_psram` device model
reports — **32 MiB**. Observed on the console:

```
esp_psram: Adding pool of 32448K of PSRAM memory to heap allocator
```

The ESP32-S3's external-memory **data** vaddr window is 32 MiB in total. Mapping all of the
detected PSRAM consumes it entirely, so the subsequent flash mmap of the partition table has no
vaddr left. The failure chain, verbatim from the run:

```
E mmap: esp_mmu_map(479): no such vaddr range
E partition: load_partitions returned 0x105        (ESP_ERR_NO_MEM)
E esp_littlefs: partition "sysbin" could not be found
```

DuneOS then reports `VFS init failed — sysbin partition missing or corrupt` and idles; the bench
scores every assertion `[MISS]` and exits 2.

**The flash image is proven fine.** The partition table decoded out of `qemu_flash_duneos.bin`
matches `partition-table.bin` byte for byte, `sysbin` is at offset `0x190000` with size
`0x100000`, and the LittleFS superblock magic is present at that offset. This is a
PSRAM-size / vaddr-budget **configuration** problem. It is not an image problem, and it is not a
regression of SPEC-leg-28 or SPEC-leg-29 — both of which are verified on the non-PSRAM board.

### A second, independent defect: the error message is misleading

`VFS init failed — sysbin partition missing or corrupt` conflates two different failures.
`esp_littlefs` returns `ESP_ERR_NOT_FOUND` when `esp_partition_find_first()` returned NULL — the
partition was **not found**, which says nothing about its contents. Genuine corruption surfaces
differently: as `-84` (`Corrupted dir pair`), which the non-PSRAM board hits harmlessly on
`/data` and recovers from by reformatting. The current wording sent this cycle's diagnosis
looking for a bad image, which is exactly where it was not.

The kernel source carrying that string is separable from the configuration fix above. Whoever
takes the configuration side may split the message fix into its own change; the two are recorded
together only because the second cost real time diagnosing the first.

## Scope

- Stop `esp32s3-qemu-psram` mapping more PSRAM than its `board.yaml` declares, so the flash mmap
  of the partition table still has vaddr available and `sysbin` is found.
- Correct the VFS init error message so a not-found partition is not reported as a corrupt one.
- Once the board passes, remove the `continue-on-error` expression from `.github/workflows/ci.yml`
  so both `qemu-smoke` matrix legs gate.

Likely fix directions, **recorded rather than chosen** — the trade-off is part of the work:

1. Pin an explicit PSRAM size and type in the board's configuration instead of
   `CONFIG_SPIRAM_TYPE_AUTO=y`, so ESP-IDF maps the declared 8 MiB whatever the model reports.
   Any change here goes through `board.yaml` / `tools/duneos-bspgen.py` — `sdkconfig.board` is
   generated and must never be hand-edited.
2. Pass QEMU a PSRAM size matching the declared 8 MiB, so auto-detection finds the right value
   and the board's configuration is left alone. This lives in `tools/dbt/qemu.py`.
3. Some combination: pin the size and assert the two agree.

Which of these is right depends on whether the divergence is the emulator's to fix or the
board's to declare — and on whether pinning the size changes anything for the hardware PSRAM
boards, which must not regress.

## Acceptance criteria

1. `python tools/dbt.py qemu --board esp32s3-qemu-psram` exits **0** with all five assertions
   matched, on a clean build directory, with no patch applied.
2. `python tools/dbt.py qemu --board esp32s3-qemu` still exits **0** with all five assertions
   matched — the non-PSRAM board does not regress.
3. The `esp_psram` line on the PSRAM board's console reports a pool consistent with the declared
   `psram_size_mb: 8`, and neither `esp_mmu_map(...): no such vaddr range` nor
   `load_partitions returned 0x105` appears in the boot log.
4. `.github/workflows/ci.yml`'s `continue-on-error` on `qemu-smoke` is **removed entirely** — not
   narrowed, not left on a different board — so both matrix legs gate.
5. The VFS init failure path distinguishes a partition that was not found from one that failed to
   mount: `ESP_ERR_NOT_FOUND` produces a message naming the missing partition and does not use
   the word "corrupt". Verified by inspection of the changed source plus one run that exercises
   it.
6. Any PSRAM configuration change reaches `sdkconfig.board` through `board.yaml` and
   `tools/duneos-bspgen.py`; no generated file is hand-edited, and all eight boards regenerate
   with only the intended diff.
7. `boards/esp32s3-devkitc` (hardware PSRAM) and `boards/lilygo-t-embed-cc1101` still build with
   no new warning after a fullclean, and their generated files change only as criterion 6 allows.
8. `./tools/.dbt-venv/bin/python -m pytest tools/dbt/tests -q` passes, with a test covering
   whatever bspgen or dbt behaviour the chosen direction introduces.

## Out of scope

- The `qemu:` key and the loader's emulator exec write path (SPEC-leg-29) — closed, unrelated,
  and exercised identically on both boards.
- The ADC / peripheral-HAL over-link (SPEC-leg-28) — closed.
- Any change to the LittleFS image builder or to `dbt flashimg`: the image is proven correct.
- Reducing DuneOS's PSRAM usage, or the loader's `#ifdef CONFIG_SPIRAM` allocation branch. This
  is about how much PSRAM is *mapped* at startup, not about who allocates from it.
- Step two of ADR 039 (booting a real board's configuration under emulation).

## Risks

- **Pinning the PSRAM size touches every PSRAM board.** `CONFIG_SPIRAM_TYPE_AUTO` is emitted by
  bspgen, so a change there reaches `esp32s3-devkitc` and `lilygo-t-embed-cc1101`, which run on
  silicon and are rarely flashed. A wrong pinned size on those is a boot failure nobody would
  notice for weeks. Criterion 7 exists for this.
- **Fixing it in `tools/dbt/qemu.py` instead makes the bench diverge from the board.** The
  emulator would then be told a size the board's own configuration never states, which is the
  same class of hidden coupling the `qemu:` key was deliberately kept narrow to avoid.
- **32 MiB may not be QEMU's only answer.** The detected size comes from the `ssi_psram` model in
  a pinned QEMU build (`esp_develop_9.2.2_20250817`); a future toolchain bump could report
  something else. Whichever direction is chosen should fail loudly on a mismatch rather than
  silently map whatever it finds.
- **The error-message fix touches kernel VFS init**, a path every board boots through. It must
  stay a message change and nothing more.

## Open questions

1. Is the 32 MiB report a QEMU model default that `idf.py qemu` can be told to override, or is it
   fixed in the machine model? This decides between fix directions 1 and 2.
2. Does the ESP32-S3 vaddr budget leave room for the declared 8 MiB *plus* the flash mmap the
   partition table and any `esp_partition_mmap` user need, or does the PSRAM board need a smaller
   declared size than 8 MiB to be viable under this bench at all?
3. Should the not-found / corrupt distinction be made only at the message level, or should the
   kernel's VFS init return distinct errno values to its caller? The latter is a wider change
   than this spec needs and would touch `main/main.c`.
