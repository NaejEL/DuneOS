import json
import subprocess
import sys
from pathlib import Path

from .constants import APP_BUILD_DIR, APP_ELF_NAME, MANIFEST_SECTION, SDK_INCLUDE, SDK_DIR, LIBDUNE_DIR
from .manifest import load_manifest, validate_manifest


def run(cmd: list, capture: bool = False) -> str:
    cmd = [str(c) for c in cmd]
    try:
        result = subprocess.run(cmd, check=True, capture_output=capture, text=True)
        return result.stdout if capture else ""
    except subprocess.CalledProcessError as e:
        if capture and e.stderr:
            print(e.stderr, file=sys.stderr)
        sys.exit(f"ERROR: command failed: {' '.join(cmd)}")
    except FileNotFoundError:
        sys.exit(f"ERROR: executable not found: {cmd[0]}")


def build_libdune(plugin, arch: str, board_cfg: dict, tc: dict) -> Path:
    """Build libdune.a for the given arch, returning the path to the archive.

    Uses mtime-based caching: rebuilds only when a source or kernel header is
    newer than the cached archive.  Cache: libdune/build/{arch}/libdune.a
    """
    lib_dir = LIBDUNE_DIR / "build" / arch
    lib_dir.mkdir(parents=True, exist_ok=True)
    lib_path = lib_dir / "libdune.a"

    sources = sorted((LIBDUNE_DIR / "src").glob("*.c"))
    if not sources:
        sys.exit("ERROR: libdune/src/ has no .c files")

    # Cache: skip rebuild if archive is newer than all sources and duneos/ headers.
    if lib_path.exists():
        archive_mtime = lib_path.stat().st_mtime
        candidates = list(sources) + sorted((SDK_INCLUDE / "duneos").glob("*.h"))
        if all(f.stat().st_mtime <= archive_mtime for f in candidates):
            return lib_path

    cc = tc["cc"]
    ar = tc.get("ar") or Path(str(cc)).parent / Path(str(cc)).name.replace("gcc", "ar")

    cflags   = plugin.cflags(board_cfg, tc)
    includes = [f"-I{SDK_INCLUDE}"]

    objects = []
    for src in sources:
        obj = lib_dir / (src.stem + ".o")
        compile_cmd = [str(cc)] + cflags + includes + ["-c", str(src), "-o", str(obj)]
        print(f"  CC [libdune]  {src.name}")
        run(compile_cmd)
        objects.append(obj)

    if lib_path.exists():
        lib_path.unlink()
    ar_cmd = [str(ar), "rcs", str(lib_path)] + [str(o) for o in objects]
    print(f"  AR [libdune]  libdune.a")
    run(ar_cmd)

    return lib_path


#: Standard launcher icon size (ADR 023). One size; the launcher owns it.
ICON_SIZE = (32, 32)


def build_app_icon(app_dir: Path, build_dir: Path) -> None:
    """Render an app's icon.png to build/icon.dr (32×32 RGB565) at build time, so
    devs ship a plain PNG and never run `dbt img convert` by hand (ADR 023).

    Non-fatal: no icon.png, or Pillow not installed, just skips — the icon is
    optional and the launcher falls back to a generic one. mtime-cached.
    """
    png = app_dir / "icon.png"
    if not png.exists():
        return
    dr = build_dir / "icon.dr"
    if dr.exists() and dr.stat().st_mtime >= png.stat().st_mtime:
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  WARN  icon.png present but Pillow missing — skipping icon.dr "
              "(pip install Pillow)")
        return
    from . import img
    print(f"  ICON  icon.png -> {dr.name} ({ICON_SIZE[0]}x{ICON_SIZE[1]})")
    img.convert(png, dr, resize=ICON_SIZE)


def build_single(app_dir: Path, plugin, arch: str, cpu: str, board_cfg: dict, tc: dict) -> bool:
    build_dir = app_dir / APP_BUILD_DIR
    build_dir.mkdir(exist_ok=True)

    try:
        manifest = load_manifest(app_dir)
        validate_manifest(manifest)
    except SystemExit as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False

    cc     = tc["cc"]
    cflags = plugin.cflags(board_cfg, tc)

    sources = sorted(app_dir.glob("*.c")) + sorted(app_dir.glob("src/*.c"))

    # Board-aware header generation (ADR 015 Pattern 2). Generates
    # <build_dir>/_board.h from the active board.yaml + this app's
    # capabilities, and exposes it via `-I<build_dir>` so apps can
    # `#include <duneos/board.h>` (which alias-includes _board.h).
    from .boardgen import write_to as _boardgen_write
    _boardgen_write(build_dir, board_cfg,
                    board_cfg.get("board", {}).get("name", "unknown"),
                    manifest.get("capabilities", []))

    # Build-time PNG→.dr icon conversion (ADR 023) — ship an icon.png, get an
    # icon.dr; non-fatal if absent or Pillow is missing.
    build_app_icon(app_dir, build_dir)

    # Capability resolution (ADR 014): apps declare needs, dbt picks the
    # board-specific source files. Resolved sources are treated identically
    # to manually-declared `sources:` entries below — they auto-pick up the
    # adjacent include/ directory.
    extra_includes = set()
    from .capabilities import resolve as _resolve_caps, CapabilityNotApplicable
    try:
        resolved = _resolve_caps(manifest.get("capabilities", []), board_cfg, SDK_DIR)
    except CapabilityNotApplicable as e:
        # The app needs a capability whose backend is served by the kernel
        # on this board — there is nothing to build in userspace. Treat as
        # a benign skip (clear any stale app.elf so flashimg won't ship it).
        stale = build_dir / "app.elf"
        if stale.exists():
            stale.unlink()
        print(f"  SKIP {e}")
        return True
    for p in resolved:
        sources.append(p)
        sdk_inc = p.parent / "include"
        if sdk_inc.is_dir():
            extra_includes.add(sdk_inc)

    # Extra sources declared in duneos.yaml — supports $SDK/ prefix
    extra_sources = manifest.get("sources", [])
    for entry in extra_sources:
        entry = str(entry)
        if entry.startswith("$SDK/"):
            p = SDK_DIR / entry[len("$SDK/"):]
        else:
            p = app_dir / entry
        if not p.exists():
            print(f"  ERROR: source not found: {p}", file=sys.stderr)
            return False
        sources.append(p)
        # Auto-add include/<duneos/> sibling of sdk/*/gfx.c → sdk/*/include
        sdk_inc = p.parent / "include"
        if sdk_inc.is_dir():
            extra_includes.add(sdk_inc)

    if not sources:
        print(f"  ERROR: no .c files in {app_dir}", file=sys.stderr)
        return False

    # Embed manifest as JSON string in a dedicated ELF section
    manifest_c = build_dir / "_manifest.c"
    # Inject the target arch so the kernel can reject cross-arch binaries.
    manifest["arch"] = arch
    manifest_json = json.dumps(manifest, separators=(",", ":"))
    manifest_c.write_text(
        f'__attribute__((section("{MANIFEST_SECTION}"), used))\n'
        f'const char _duneos_manifest[] = {json.dumps(manifest_json)};\n'
    )
    sources = [manifest_c] + list(sources)

    includes = [f"-I{SDK_INCLUDE}", f"-I{build_dir}", f"-I{app_dir}"]
    for inc in sorted(extra_includes):
        includes.append(f"-I{inc}")

    objects = []
    for src in sources:
        obj = build_dir / (src.stem + ".o")
        compile_cmd = [str(cc)] + cflags + includes + ["-c", str(src), "-o", str(obj)]
        print(f"  CC  {src.name}")
        try:
            run(compile_cmd)
        except SystemExit:
            return False
        objects.append(obj)

    libdune = build_libdune(plugin, arch, board_cfg, tc)

    elf = build_dir / APP_ELF_NAME
    link_cmd = (
        [str(cc)]
        + cflags
        + plugin.ldflags(board_cfg)
        + [str(o) for o in objects]
        + [str(libdune)]
        + ["-o", str(elf)]
    )
    print(f"  LD  {elf.name}")
    try:
        run(link_cmd)
    except SystemExit:
        return False

    print(f"  OK  {elf.stat().st_size} bytes")
    return True


def clean_single(app_dir: Path) -> None:
    import shutil
    from .constants import DUNEOS_ROOT
    build_dir = app_dir / APP_BUILD_DIR
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"  cleaned  {app_dir.relative_to(DUNEOS_ROOT)}")
