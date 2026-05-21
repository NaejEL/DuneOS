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
