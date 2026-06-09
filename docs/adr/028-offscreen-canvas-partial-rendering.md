# ADR 028 — Offscreen canvas & partial rendering (`gfx_canvas`)

**Status:** Accepted · 2026-06-07

## Context

libgfx offers two drawing modes ([[ADR-024]] companion, Phase 24.10):

- `GFX_MODE_BUFFERED` — a full-screen RGB565 back-buffer; `gfx_flush` pushes the
  whole frame atomically. No flicker, but 240×135 = 63 KiB, which does not fit
  alongside the launcher on the no-PSRAM CardPuter ([[ADR-008]], [[ADR-025]]).
- `GFX_MODE_STREAM` — no back-buffer; each draw writes straight to the panel.
  Tiny RAM, but the only way to "redraw a frame" is clear-then-repaint, and the
  eye catches the blank intermediate. The games (snake, tetris) flickered badly.

The big ESP32 UI libraries do not have this problem because they never show the
panel a half-drawn frame: TFT_eSPI *sprites*, LVGL *partial buffers*, and
esp_lcd *bounce buffers* all compose a region in RAM and push it in one write.
DuneOS needed the same primitive, RAM-bounded so it works without PSRAM.

## Decision

**Add an offscreen canvas to libgfx: a `gfx_ctx` with no backing device.** Because
every `gfx_*` primitive already draws into `ctx->buf` with `ctx->width` as stride
when the mode is BUFFERED, a canvas is simply a BUFFERED context whose backend is
`GFX_BACKEND_NONE` and whose buffer the app owns. All existing primitives work on
it unchanged; nothing in the draw path needed special-casing.

```c
gfx_ctx_t *gfx_canvas_new(int w, int h);                 /* malloc w*h*2 */
void       gfx_canvas_present(gfx_ctx_t *display,
                              const gfx_ctx_t *canvas, int x, int y);
void       gfx_canvas_free(gfx_ctx_t *canvas);
```

`gfx_canvas_present` is one `gfx_blit` of the whole canvas → a single
`disp_write_area` transaction on a STREAM display. The panel never shows a
partial frame, so no flicker — without a full-screen back-buffer.

**RAM is bounded to the region that actually moves**, not the screen. That bound
is the whole point: a tetris well is 60×120 = 14 KiB, vs 63 KiB for a full frame.

### Two techniques, picked per app

The canvas is one of two ways to kill flicker; the right choice depends on how
much of the frame changes per tick:

- **Incremental (no canvas)** — when only a few cells change, redraw just those
  cells straight to the STREAM panel. Each is one small atomic write, already
  flicker-free, **zero extra RAM**. `snake` uses this: erase the vacated tail,
  recolor the old head, draw the new head. `game_cell_at(gfx_ctx_t*, …)` draws
  one cell onto any context for exactly this.
- **Canvas** — when most of the region changes per frame (rotations, line clears),
  compose the whole region in a canvas and present it. `tetris` uses this for its
  well; static chrome (border, title) is drawn once outside the present rect.

## Consequences

- Flicker eliminated on the games with no full-screen back-buffer; validated on
  CardPuter hardware (2026-06-07).
- A reusable libgfx primitive, not a per-app hack — the libui widgets and any
  animated view (e.g. `waves` pan/zoom, roadmapped) can adopt it.
- The app must size and free the canvas, and declare `heap_size` to cover it
  (tetris: 14 KiB well → 32 KiB heap with slack). This is *real reserved RAM* —
  the per-app heap pool is malloc'd whole at launch and not shared ([[ADR-025]]
  memory model), so canvases must stay as small as the moving region allows.
- `gfx_close` must not touch a device for `GFX_BACKEND_NONE`; canvases are freed
  with `gfx_canvas_free`, not `gfx_close`.

## Alternatives considered

- **Always use BUFFERED.** Rejected on the CardPuter: 63 KiB back-buffer plus the
  launcher does not fit ([[ADR-025]]).
- **Incremental everywhere.** Works for snake; for tetris the per-frame diff
  (line clears shift the whole well) is complex and risks multi-write tearing.
  The 14 KiB canvas buys correctness and simplicity.
- **A kernel-side compositor / dirty-rectangle layer.** Far heavier than a
  userspace buffer + one blit; against the userspace-UI line ([[ADR-009]]).

## Cross-references

- [[ADR-024]] — board-driven responsive views (canvas size derives from geometry).
- [[ADR-008]] / [[ADR-025]] — memory budget and the per-app heap model that bounds
  canvas size.
- [[ADR-009]] — UI stays in userspace (no kernel compositor).
