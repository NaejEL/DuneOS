"""
img — convert PNG/JPEG/etc. to DuneOS '.dr' raster format.

The '.dr' format is an 8-byte header + raw RGB565 pixel data (host-endian on
ESP32, i.e. little-endian). Apps load it via <duneos/image.h> and blit it
through <duneos/gfx.h> at zero decode cost.

We rely on Pillow only at conversion time; the apps themselves don't need it.
Pillow import is lazy so `dbt buildall` / `dbt flashimg` keep working on
machines without it.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_MAGIC          = 0xD12E
_FMT_RGB565     = 0
_HEADER_FMT     = "<HHHH"   # little-endian: magic, w, h, fmt
_HEADER_SIZE    = struct.calcsize(_HEADER_FMT)


def _to_rgb565(px) -> int:
    r, g, b = px[0], px[1], px[2]
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def _parse_size(spec: str) -> tuple[int, int]:
    try:
        w_s, h_s = spec.lower().split("x", 1)
        return int(w_s), int(h_s)
    except (ValueError, AttributeError):
        sys.exit(f"ERROR: invalid --resize value '{spec}' — expected WxH")


def convert(input_path: Path, output_path: Path,
            resize: tuple[int, int] | None = None,
            background: tuple[int, int, int] = (0, 0, 0)) -> None:
    try:
        from PIL import Image
    except ImportError:
        sys.exit(
            "ERROR: Pillow is required for 'dbt img convert'.\n"
            "  pip install Pillow"
        )

    if not input_path.exists():
        sys.exit(f"ERROR: input not found: {input_path}")

    im = Image.open(input_path)

    # Composite over a solid background to flatten any alpha channel — the
    # .dr format has no alpha so a transparent PNG would otherwise lose its
    # silhouette when blitted on a coloured display.
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")

    if resize is not None:
        im = im.resize(resize, Image.LANCZOS)

    w, h = im.size
    if w > 0xFFFF or h > 0xFFFF:
        sys.exit(f"ERROR: image too large ({w}x{h}) — max 65535 per side")

    pixels = im.tobytes()   # RGB packed
    out = bytearray(_HEADER_SIZE + w * h * 2)
    struct.pack_into(_HEADER_FMT, out, 0, _MAGIC, w, h, _FMT_RGB565)

    o = _HEADER_SIZE
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[o]     = c & 0xFF
        out[o + 1] = (c >> 8) & 0xFF
        o += 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(out))

    print(f"  {input_path.name} → {output_path}")
    print(f"  {w}×{h} RGB565, {_HEADER_SIZE + w * h * 2} bytes")


def cmd_img_convert(args) -> None:
    resize = _parse_size(args.resize) if args.resize else None
    bg = (0, 0, 0)
    if args.background:
        try:
            r, g, b = (int(x) for x in args.background.split(","))
            bg = (r, g, b)
        except ValueError:
            sys.exit("ERROR: --background expects 'R,G,B' (0-255 each)")
    convert(Path(args.input), Path(args.output), resize=resize, background=bg)


# ---------------------------------------------------------------------------
# Procedural splash renderer
#
# Reproduces the apps/user/splash fallback art (gradient + dune silhouettes
# + 8×8 wordmark with drop shadow). Same maths as splash.c so the keepsake
# PNG is byte-equivalent to what the CardPuter renders.
# ---------------------------------------------------------------------------

# A compact subset of font8x8_basic — only the characters the splash actually
# draws. Source: sdk/display/include/duneos/font8x8.h. LSB-left convention
# (bit 0 = leftmost pixel of the row).
_FONT_GLYPHS = {
    ' ': (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    'D': (0x1F, 0x33, 0x33, 0x33, 0x33, 0x33, 0x1F, 0x00),
    'O': (0x3E, 0x63, 0x63, 0x63, 0x63, 0x63, 0x3E, 0x00),
    'S': (0x1E, 0x33, 0x07, 0x0E, 0x38, 0x33, 0x1E, 0x00),
    'b': (0x07, 0x06, 0x06, 0x3E, 0x66, 0x66, 0x3B, 0x00),
    'e': (0x00, 0x00, 0x1E, 0x33, 0x3F, 0x03, 0x1E, 0x00),
    'n': (0x00, 0x00, 0x1F, 0x33, 0x33, 0x33, 0x33, 0x00),
    'o': (0x00, 0x00, 0x1E, 0x33, 0x33, 0x33, 0x1E, 0x00),
    't': (0x08, 0x0C, 0x3F, 0x0C, 0x0C, 0x2C, 0x18, 0x00),
    'u': (0x00, 0x00, 0x33, 0x33, 0x33, 0x33, 0x6E, 0x00),
}


def _draw_text_8x8(im, x: int, y: int, text: str,
                   fg: tuple[int, int, int],
                   bg: tuple[int, int, int]) -> None:
    """Emulate gfx_text(): fill an 8×len cell with bg, plot fg pixels per glyph."""
    pixels = im.load()
    W, H = im.size
    for i, ch in enumerate(text):
        glyph = _FONT_GLYPHS.get(ch, _FONT_GLYPHS[' '])
        gx0 = x + i * 8
        for row in range(8):
            byte = glyph[row]
            py = y + row
            if py < 0 or py >= H:
                continue
            for col in range(8):
                px = gx0 + col
                if px < 0 or px >= W:
                    continue
                if (byte >> col) & 1:
                    pixels[px, py] = fg
                else:
                    pixels[px, py] = bg


def render_splash(width: int, height: int):
    try:
        from PIL import Image
    except ImportError:
        sys.exit(
            "ERROR: Pillow is required for 'dbt img splash'.\n"
            "  pip install Pillow"
        )

    im = Image.new("RGB", (width, height), (0, 0, 0))
    pixels = im.load()

    # 1) Vertical sand gradient — same coefficients as splash.c.
    for y in range(height):
        r = 60 + (y * 160) // height
        g = 40 + (y * 110) // height
        b = 20 + (y * 45)  // height
        for x in range(width):
            pixels[x, y] = (r, g, b)

    # 2) Dune silhouettes.
    baseline = (height * 7) // 10
    dune = (50, 30, 15)
    cx = width // 2

    def fill_rect(px: int, py: int, pw: int, ph: int):
        for yy in range(max(py, 0), min(py + ph, height)):
            for xx in range(max(px, 0), min(px + pw, width)):
                pixels[xx, yy] = dune

    for dy in range(22):
        hw = 22 - dy
        fill_rect(cx - 60 - hw, baseline - dy, hw * 2, 1)
        fill_rect(cx + 60 - hw, baseline - dy, hw * 2, 1)
    for dy in range(32):
        hw = 32 - dy
        fill_rect(cx - hw, baseline - dy - 6, hw * 2, 1)

    # 3) Wordmark "DuneOS" + drop shadow, then "boot" tag.
    title    = "DuneOS"
    title_w  = 8 * len(title)
    title_x  = cx - title_w // 2
    title_y  = 14
    title_bg = (60, 40, 20)

    _draw_text_8x8(im, title_x + 1, title_y + 1, title, (30, 20, 10), title_bg)
    _draw_text_8x8(im, title_x,     title_y,     title, (255, 255, 255), title_bg)

    tag    = "boot"
    tag_w  = 8 * len(tag)
    _draw_text_8x8(im, cx - tag_w // 2, title_y + 14, tag,
                   (255, 230, 180), title_bg)

    return im


def _image_to_dr_bytes(im) -> bytes:
    w, h = im.size
    out = bytearray(_HEADER_SIZE + w * h * 2)
    struct.pack_into(_HEADER_FMT, out, 0, _MAGIC, w, h, _FMT_RGB565)
    o = _HEADER_SIZE
    pixels = im.tobytes()
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[o]     = c & 0xFF
        out[o + 1] = (c >> 8) & 0xFF
        o += 2
    return bytes(out)


def cmd_img_splash(args) -> None:
    if args.size:
        w, h = _parse_size(args.size)
    else:
        w, h = 240, 135   # CardPuter default

    im = render_splash(w, h)

    if args.scale and args.scale > 1:
        from PIL import Image
        im_out = im.resize((w * args.scale, h * args.scale), Image.NEAREST)
    else:
        im_out = im

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.suffix.lower() == ".dr":
        out.write_bytes(_image_to_dr_bytes(im))
        print(f"  splash {w}×{h} → {out}  ({out.stat().st_size} bytes)")
    else:
        im_out.save(out)
        ow, oh = im_out.size
        print(f"  splash {ow}×{oh} → {out}")

    if args.also_dr:
        dr_path = out.with_suffix(".dr")
        dr_path.write_bytes(_image_to_dr_bytes(im))
        print(f"  splash {w}×{h} → {dr_path}  ({dr_path.stat().st_size} bytes)")
