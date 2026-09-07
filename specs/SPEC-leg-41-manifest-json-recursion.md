# LEG-41 — Unbounded cJSON recursion parses attacker-controlled manifests on the boot stack
Status: APPROVED

## Context

Found 2026-09-06 during the SPEC-leg-05 verification, by a reviewer chasing a
wrong number in a stack-depth comment. It is not a LEG-05 regression — the code
path is unchanged and predates the split.

`duneos_loader_load()` parses each `.dap`'s embedded `.duneos_manifest` JSON with
`cJSON_ParseWithLength()` (`loader.c:1124`). cJSON's descent is mutually
recursive — `parse_value -> parse_object/parse_array -> parse_value` — and the
vendored copy caps nesting at `CJSON_NESTING_LIMIT`, **1000**
(`third_party/cjson/cJSON.h:137`).

Frame sizes, measured with `xtensa-esp32s3-elf-objdump` on the built
`cJSON.c.obj` (`entry a1, N` immediate, so windowed-register save area included):

| frame | bytes |
| --- | --- |
| `parse_value` | 32 |
| `parse_object` | 32 |
| `parse_array` | 32 |
| `parse_string` | 48 |

So **~64 B of stack per nesting level**, and cJSON will happily take 1000 of
them — about **64 KiB**.

**480 B is the floor, not the one-nesting-level case.** A manifest *is* a JSON
object, so `cJSON_ParseWithLength(32) + cJSON_ParseWithLengthOpts(80) +
parse_value(32) + parse_object(32) + parse_value(32) + parse_string(48)` is
unavoidable — that chain sums to 256 B — so even a flat `{"name":"x"}` reaches
`load(160) + extract_manifest(64) + 256 = 480 B` on this path. Every further
nesting level adds 64 B on top of that, with no bound below 1000.

The boot scan parses the manifest of **every** `.dap` it finds, on `main_task`.
Three boots were measured on the m5stack-cardputer (2026-09-06), leaving
924 / 860 / 844 B free; the budget is the **worst** of them. `main_task`'s real
stack is 5120 B (`ESP_TASK_MAIN_STACK` = the board's 4608 B Kconfig value plus
the 512 B `TASK_EXTRA_STACK_SIZE` of this picolibc build), so the worst peak is
**4276 B of 5120 B, leaving 844 B raw and 784 B after the 60 B end-of-stack
watchpoint**. At 64 B per level, roughly **12 further nesting levels consume the
entire margin** — and that is on top of the 480 B floor above, not instead of it.

The manifest is file content: a `.dap` on the SD card, which any user can drop
there and which the boot scan opens without the file having been run. This is
therefore an unbounded, file-driven stack descent on the boot path.

**This is the LEG-37 failure mode exactly** — an overflow of `main_task` runs
into the adjacent heap and surfaces as TLSF free-list corruption and a watchdog
reboot loop thousands of instructions later, never as a stack report. The
difference is that LEG-37 needed a careless refactor and this needs a file.

`CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` (`sdkconfig.defaults:57`) is armed, so
the failure should now name itself rather than corrupting silently — that is
mitigation of the symptom, not of the cause.

## Scope

Bound the manifest parse so that no file content can drive the boot stack past
a known ceiling.

**Direction chosen by the Product Owner (2026-09-06): a pre-parse depth check we
own.** The `.duneos_manifest` bytes are scanned by our own non-recursive,
constant-stack pass *before* `cJSON_ParseWithLength()` is ever called; a manifest
whose nesting exceeds the derived limit is rejected there, with a named reason,
having spent no descent at all. This satisfies "before the recursion" literally,
and it survives a cJSON submodule update, unlike a patch to `cJSON.h`.

A `-DCJSON_NESTING_LIMIT=` in the component's build definition MAY be added as a
second line of defence, but it is not the primary mechanism and must not be the
thing the acceptance criteria rest on.

Explicitly not chosen: moving the parse off `main_task` (leaves the recursion
itself unbounded and adds a task to the boot path).

## Acceptance criteria

- [x] A `.dap` whose manifest nests deeper than the chosen limit is REJECTED with
      a named reason, not parsed.
- [x] The rejection happens before the recursion, not by surviving it.
- [x] The limit is derived from a stated stack budget, and the derivation is
      written down where the next person will find it.
- [x] A host corpus case pins it: a deeply nested manifest must fail the parse,
      and the case must fail loudly if the bound is removed (mutation-tested).
- [x] All 57 real `apps/**/build/app.elf` manifests still parse — the bound must
      not reject legitimate output.
- [ ] **BLOCKED ON HARDWARE** — `main_task` peak on the cardputer re-measured with
      the fix and reported as a number. Nobody in the implementing session had the
      board. Deliberately left unticked; no number was estimated or fabricated.
      To close it, with the CardPuter attached:
      `python tools/dbt.py flash kernel && python tools/dbt.py flashimg &&
      python tools/dbt.py monitor`, then `free` at the DuneOS shell — it prints
      `boot stack peak N of 5120`. Baseline to compare against: peak 4276 B,
      784 B usable margin. The derivation predicts no change, since the depth-4
      bound holds the manifest chain at or below the pre-existing 672 B maximum.

## Out of scope

Replacing cJSON; the loader split (LEG-05); hardening other cJSON call sites
unless they are also on the boot path.

## Risks

Lowering a vendored library's compile-time limit must be done where it survives
a submodule update — a patch to `cJSON.h` in `third_party/` would be lost. Prefer
a `-DCJSON_NESTING_LIMIT=` in the component's build definition, or a pre-parse
check we own.
