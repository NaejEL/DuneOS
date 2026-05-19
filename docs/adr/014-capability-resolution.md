# ADR 014 — Capability-based source resolution

**Status:** Accepted · 2026-05-20

## Context

A DuneOS app that draws on screen has historically declared its dependencies as a literal list of source files in `duneos.yaml`:

```yaml
sources:
  - $SDK/display/libdisp.c
  - $SDK/display/libst7789.c
```

This works on the M5Stack CardPuter (ST7789 display) but fails the portability test. To run the same app on a board with an ILI9341 or SSD1306, the app's manifest must be edited — even though the app's *source code* is already chip-agnostic (it talks only to `disp.h`). The board-specific knowledge has leaked from the BSP into the app.

The kernel solved an analogous problem with [ADR-010](010-arch-accelerators.md) Pattern A: apps target a capability (`hal_encoder`), the kernel's `arch.cmake` selects the matching backend at build time (PCNT on ESP32, PIO on RP2040, GPIO fallback elsewhere). We want the same model on the userspace side, with `dbt` filling the role `arch.cmake` plays for the kernel.

A related concern: ADR-009 forbids hardcoding driver chip identifiers in shared code. Today the violation lives in app manifests, which `dbt build` reads without translation. Adding new display chips (ILI9341, SSD1306, EPD chips) requires touching every app's yaml. That's a multiplier on every new board.

## Decision

Manifests declare **capabilities** they need; `dbt build` resolves each capability into concrete source files by reading the active `board.yaml`.

**New manifest field:**

```yaml
capabilities:
  - display
```

**Capability map** (in `tools/dbt/capabilities.py`):

```python
CAPABILITY_MAP = {
    "display": {
        "board_key": ["display", "driver"],
        "sources": [
            "{sdk}/display/libdisp.c",
            "{sdk}/display/lib{driver}.c",
        ],
        "description": "...",
    },
    # future: input, sensor:battery, audio:i2s_out, net:wifi_sta
}
```

**Resolution algorithm:**

1. `dbt build` parses the app's `capabilities:` list.
2. For each entry, look up the spec in `CAPABILITY_MAP`.
3. Read the active board's yaml; walk `board_key` to get the chip identifier (e.g. `st7789`).
4. Expand each source template with `{sdk}` = repo's `sdk/` and `{driver}` = chip identifier.
5. Resolved paths are appended to the app's source list, alongside any explicit `sources:` entries. The build proceeds as before.

**Error handling — fail at build time, not at runtime:**

- Unknown capability name → `dbt build` exits with the list of known capabilities and a pointer to this ADR.
- Board lacks the required `board.yaml` key (e.g. an app needs `display` but `board.yaml` has no `display.driver`) → `dbt build` exits with a precise message identifying the missing key.
- Capability resolves but the source file (`lib{driver}.c`) doesn't exist in the SDK → `dbt build` exits asking the maintainer to either add `lib{driver}.c` or correct the board's driver name.

This is consistent with [ADR-006](006-manifest-extensibility.md): an unknown field (`capabilities`) read by an old `dbt` is silently ignored — backward compatible. A new `dbt` reading an old manifest (no `capabilities:` field) treats it as empty — equally backward compatible.

**SDK layout convention:**

For a capability `<cap>` with driver `<drv>`, the SDK must provide:

```
sdk/<cap>/include/duneos/<cap>.h        chip-agnostic API (apps include this)
sdk/<cap>/lib<cap>.c                    dispatch layer; defined symbol consumes ops vtable
sdk/<cap>/lib<drv>.c                    chip-specific backend; defines const duneos_<cap>_ops
```

Apps include the agnostic header (`<duneos/disp.h>`); they never reference the chip name in code.

### Initial scope

Only `display` is implemented in this ADR. Other capabilities are planned but explicitly out of scope until a real use surfaces:

- `input` — needs `uinput`-style kernel infrastructure before it can be userspace (currently `kb_iomatrix.c` / `btn_gpio.c` live in the kernel — see ROADMAP Phase 24 debt item #5). When that lands, apps declare `capabilities: [input]` and dbt links the right chip backend.
- `sensor:battery` — `libbq27220.c` already exists in `sdk/sensor/`; capability resolution will follow once we add a second fuel-gauge chip (no need today on one-chip target).
- `audio:i2s_out`, `net:wifi_sta` — speculative; defer until concrete drivers exist.

## Consequences

- Apps become genuinely board-portable. Moving `g_shell` from CardPuter to a hypothetical ILI9341 board requires zero edit — the board's yaml declares `display.driver: ili9341`, the SDK ships `sdk/display/libili9341.c`, `dbt build` does the rest.
- Adding a new display chip = ship `sdk/display/lib<chip>.c` and one entry in a board's yaml. Zero changes to any app manifest, ADR, or capability map.
- Adding a new capability category (e.g. `input` post-uinput) = one entry in `CAPABILITY_MAP` and the matching SDK layout. Apps that need it add one yaml line.
- App manifests become smaller and more declarative. The `sources:` field stays useful for app-private aggregations (e.g. an app split across multiple `.c` files in `src/`), but stops being a place to make board-specific decisions.
- The error model is fail-fast: a missing capability or mistyped capability name is caught at `dbt build`, not at runtime where it would manifest as a confusing crash deep in the app's first display call.
- This ADR doesn't change anything that ships in the binary; it only changes who decides which chip backend gets compiled in. The resulting `.dap` for a given board is byte-identical to what the old manifest produced.

## Alternatives

- **Aggregator file `libdisp_auto.c` generated by dbt** — rejected. Adds a generated-file step (build cache invalidation, diff noise), and doesn't extend cleanly beyond display.
- **Folder-as-source convention** (`sources: [$SDK/display]`) — rejected. Confuses "file" and "directory" semantics in the same field; harder to read; the same intent is more explicit as a separate `capabilities:` list.
- **Variable substitution in `sources:`** (`$SDK/display/lib${DISPLAY_CHIP}.c`) — rejected. Works for display but doesn't scale: every new chip type would need its own environment variable contract. Capability map centralises this in one place.
- **Keep the hardcoded sources list** — rejected. The pain compounds with every new board variant; ADR-009's portability promise can't hold while board-specific files live in app manifests.
- **Silent fallback to a stub when board lacks the capability** — rejected. Apps that silently use a stub display would crash at first draw or behave inconsistently across boards. A build-time error is honest about what the board does and doesn't support.
