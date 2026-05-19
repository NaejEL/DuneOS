# M5Stack Global Innovation Contest 2026 — DuneOS submission plan

**Status:** Planned · 2026-05-20
**Contest:** https://m5stack.com/global-innovation-contest-2026
**Submission deadline:** August 7, 2026 (11:59 PM PST)
**Announcement:** August 31, 2026

This plan absorbs the work of Phases 24.7 + 25 (minimum viable) and freezes the roadmap there until the contest results are announced. Phases 26-29 resume from 2026-09 onwards.

---

## Strategy

**Position DuneOS as a product, not an OS.** The 2025 winners (Past Camera, Autonomous Buoy, TOTP Authenticator, Relativistic Clock) were tangible gadgets — judges did not reward enabler infrastructure. The contest brief explicitly notes *"Notable absence: Pure software-only projects; winners leverage M5Stack's physical hardware capabilities."*

The submission frames the CardPuter + DuneOS combination as a **hacker handheld with drag-and-drop apps**, demonstrated through a curated app set that shows the platform's value. The OS is the technical depth, the apps are the emotional hook.

Reference: NaejEL's [flipperzero-i2ctools](https://github.com/NaejEL/flipperzero-i2ctools) won a Flipper Zero through a similar pattern (focused forensic tool, well-presented). The DuneOS submission scales that approach to a portfolio: forensic tool + games + scripting + launcher.

## Timeline

| Window | Work | Output |
|---|---|---|
| **2026-05-20 → 06-05** (2 weeks) | Phase 24 debt finalisation: `hal_encoder` (PCNT), ST7789 consolidation, BQ27220 → userspace. Phase 24.7 Safe boot: circuit breaker + recovery pin + `dbt flash sysbin --safe`. | Stable kernel base, no risk of bricking during dev. |
| **2026-06-05 → 06-19** (2 weeks) | Phase 25 minimum viable: `dbt system check` + `system.yaml` + **manifest `icon:` field** + iconconv script. Skip the heavier `profile.yaml` orchestration; not on critical path. | Tooling for image assembly + per-app icons. |
| **2026-06-19 → 07-17** (4 weeks) | Killer-app development. See app list below. Includes graphical launcher to replace/wrap `g_shell`. | 5-6 working apps with icons. |
| **2026-07-17 → 07-31** (2 weeks) | Polish, photos, video demo, Hackster project page, README contest section. | Submission packaged. |
| **2026-07-31 → 08-07** (1 week) | Buffer for unexpected. | Safety margin. |
| **2026-08-08 → 08-31** | Judging period — no code expected on DuneOS. | — |
| **2026-09-01 onwards** | Roadmap resumes at Phase 26 (OSAL). | — |

**Hard freeze rule:** no work on Phase 26 (OSAL) before 2026-09-01. The supervisor / task / klog refactor is too disruptive to risk during the 3 weeks before submission.

## App portfolio

Five apps + a launcher. Each shipped as a `.dap` with an icon, sideloadable via USB MSC.

| App | What it does | Effort | Notes |
|---|---|---|---|
| **`i2cscope`** | I²C scanner, register read/write, hexdump. Inspired by flipperzero-i2ctools. | 1 week | Uses `/dev/i2c-0` (already exposed). Shows forensic/utility side. |
| **`snake`** | Snake on 240×135 with libgfx. Score persisted to `/sd/snake.score`. | 3 days | Visual, universally appreciated. |
| **`tetris`** | 10×20 grid, CardPuter keymap (←/→/↓/rotate). | 1 week | Second game shows the launcher's value (switch without reflash). |
| **`lua`** | Lua REPL with bindings to GPIO, I²C, framebuffer. | 1-2 weeks | Choice of implementation pending (see "Open decisions" below). |
| **`launcher`** | Graphical grid of installed apps with icons. Arrow-key nav, Enter to launch. Replaces or wraps `g_shell`. | 1 week | The visual centrepiece. |
| **`wifi_scan`** (bonus) | Scan APs, RSSI bars. | 2 days | Shows the network stack works. Optional, depends on time. |

**Total dev effort:** ~5-6 weeks. Fits in the 4-week window with the bonus app deferred if needed.

## Manifest icon field

Add to `duneos.yaml`:

```yaml
name: i2cscope
version: "0.1.0"
icon: icon.raw          # 32×32 RGB565, 2048 bytes, little-endian
icon_caption: "I2C Tools"
```

**Format:** 32×32 pixels, RGB565 little-endian, raw bytes (no header). 2048 bytes per icon.

**Pipeline:**
- Author drops an `icon.png` (or `.gif`/`.bmp`) next to their `.c`
- `tools/dbt/iconconv.py` converts via PIL: resize to 32×32 → RGB565 → write `icon.raw`
- `dbt build` embeds `icon.raw` into a new ELF section `.duneos_icon` (parallel to `.duneos_manifest`)
- The launcher reads `.duneos_icon` via the loader's section lookup, blits with libgfx

**Compatibility:** apps without `icon:` get a default icon (the launcher provides a fallback). Consistent with [ADR 006](adr/006-manifest-extensibility.md) — additive field, no ABI bump.

## Hackster submission

The Hackster.io project page is the primary deliverable. Required components per the contest brief:

- Clear description + hardware list
- Video demo (60-90 seconds, see arc below)
- Build instructions or GitHub repository link
- Explanation of how the M5Stack controller is used

### Demo video arc

1. **0-10s** — Plug CardPuter via USB to a laptop. Webcam shows it mounting as a USB drive. Drag three `.dap` files into the `apps/` folder.
2. **10-25s** — Unplug. CardPuter boots. Launcher displays installed apps with icons.
3. **25-45s** — Open `i2cscope`, scan a connected sensor (BMP280 on breadboard), dump a register, hexdump on screen.
4. **45-60s** — Back to launcher. Open Lua REPL. Type `for i=1,5 do print(i*i) end`. Show output.
5. **60-75s** — Back to launcher. Play 5 seconds of Snake. Score visible.
6. **75-90s** — Caption: *"All apps from a single firmware. Open source. DuneOS."*

### Pitch text (Hackster)

**Title:** *DuneOS — Drop-in apps for your CardPuter*

**Subtitle:** *Boot once. Sideload apps over USB. Hack like it's a Flipper Zero, but open.*

**Body (200 words):**

> The M5Stack CardPuter is a $30 hacker handheld waiting to happen — but every new feature means reflashing the firmware. Want an I2C scanner? Reflash. Want to play Snake? Reflash. Want both? Pick one.
>
> DuneOS fixes that. Boot the firmware once, plug the device into USB, and **drag-and-drop apps onto it like a USB stick**. Each app is a standalone ELF binary written in plain C using POSIX (`open`, `read`, `ioctl`, `socket`). The kernel loads, sandboxes, and runs them — no firmware update needed.
>
> What ships here:
>
> - **i2cscope** — full I²C bus explorer, scanner, register dumper. The Bus Pirate in your pocket.
> - **lua** — Lua REPL with bindings to GPIO, I²C, framebuffer. Hack hardware in 5 lines.
> - **snake** + **tetris** — because the CardPuter has a real keyboard, why not.
> - **launcher** — graphical app grid, navigated with arrow keys.
>
> Under the hood: a POSIX-style kernel that ships in ~1 MB, an ELF loader inspired by Flipper Zero's `.fap` format, a per-app heap so a crashing app kills only itself, USB Mass Storage + CDC console without rebooting. 14 published Architecture Decision Records ([docs/adr/](../docs/adr/)) for anyone wanting to port the kernel elsewhere.
>
> **Build your next ESP32 idea as an app, not as a firmware fork.**

## Why this matches the winning pattern

| Contest criterion | DuneOS submission |
|---|---|
| **Hardware mandate** (must use M5Stack controller) | CardPuter is the primary target ✓ |
| **Creativity & Originality** | First drag-and-drop sideloadable OS for an M5Stack device. ADR-driven design unique in this space. |
| **Functionality & Execution** | 5 working apps + launcher, demoed end-to-end. Built on hardware that boots reliably. |
| **Documentation & Presentation** | The 14 ADRs are a differentiator no other submission will have. README, demo video, build instructions ready. |
| **Impact & Usefulness** | Turns a niche dev board into a usable hacker handheld for any owner. Lowers the barrier to ESP32 hacking from "fork the firmware" to "write a 50-line app". |

**Realistic prize targets:**

- **3rd Prize ($100 + device)** — very plausible with 5 apps + polished demo
- **2nd Prize ($200 + device)** — plausible if the Lua REPL works smoothly and the launcher visual lands
- **Grand Prize ($1000 + device)** — would need a uniquely WOW killer-app on top (the platform alone doesn't carry it). Not the primary target.

## Open decisions

These can be deferred until the matching app starts development:

- **Lua implementation choice.** Lua 5.4 official (~150 KB code, large community, well-documented API) is the default. eLua / Lua-RTOS exist but are less maintained. MicroPython would pivot to Python (~400 KB, more popular in hardware hacking but heavier). Decide at start of `lua` app week.
- **Icon format encoding.** Default: 32×32 RGB565 raw little-endian, 2 KB per icon. Compression (RLE / palette) not justified at this scale. Decide before the `iconconv.py` tool ships.
- **Bonus apps.** `wifi_scan`, `btn_test` if time permits; cut if behind schedule.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Phase 24 debt slips past 2026-06-05 | Cut scope — keep the 4 items as-is, defer hal_encoder migration of input drivers to post-contest. |
| Lua interpreter too heavy / slow on app pool | Fallback to a tiny custom interpreter or skip Lua, replace with a third game. App slot stays at 5 apps + launcher. |
| Launcher visual underwhelming | Ship without animations first; add polish if time. Icon grid alone is the must-have. |
| CardPuter crashes during demo recording | Phase 24.7 safe boot is mandatory before recording. The circuit breaker protects against a runaway service. |
| Video recording quality | Coordinate with whoever has good lighting/camera. Record multiple takes. Caption the screen content if filming is grainy. |

## After 2026-08-31

Win or not, the contest sprint forces the project to ship a usable product on top of the kernel. Post-contest:

- Resume roadmap at Phase 26 (OSAL).
- The 5 apps remain in `apps/system/` (i2cscope, snake, tetris, lua) and `apps/user/` (launcher could move to `apps/system/` as the default).
- The `icon:` manifest field and `iconconv.py` are durable additions.
- If the project wins anything, the Hackster page and demo video become the project's primary marketing surface for years.

## Cross-references

- [ROADMAP_v2.md](../ROADMAP_v2.md) — main roadmap (this plan suspends phases 26-29 until 2026-09)
- [ADR 006](adr/006-manifest-extensibility.md) — manifest extensibility (justifies the additive `icon:` field)
- [ADR 011](adr/011-threat-model.md) — threat model (referenced briefly in pitch as a transparency point)
- [ADR 012](adr/012-test-strategy.md) — test strategy (NOT activated for the contest; tests resume Phase 26)
- [backlog.md](backlog.md) — unscheduled ideas (OTA, coredump, etc. — all stay in backlog through the contest)
