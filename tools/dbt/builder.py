import json
import os
import subprocess
import sys
from pathlib import Path

from .constants import (APP_BUILD_DIR, APP_ELF_NAME, DUNEOS_ROOT, MANIFEST_SECTION,
                        SDK_INCLUDE, SDK_DIR, LIBDUNE_DIR)
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


#: Standard app icon size (ADR 023). One size; the launcher scales for previews.
ICON_SIZE = (48, 48)


def build_app_icon(app_dir: Path, build_dir: Path) -> None:
    """Render an app's icon.png to build/icon.dr (ICON_SIZE RGB565) at build time,
    so devs ship a plain PNG and never run `dbt img convert` by hand (ADR 023).

    Non-fatal: no icon.png, or Pillow not installed, just skips — the icon is
    optional and the launcher falls back to a generic one. Cached on mtime *and*
    the stored .dr dimensions (so changing ICON_SIZE forces a reconvert).
    """
    import struct
    png = app_dir / "icon.png"
    if not png.exists():
        return
    dr = build_dir / "icon.dr"
    if dr.exists() and dr.stat().st_mtime >= png.stat().st_mtime:
        try:
            _, w, h, _ = struct.unpack_from("<HHHH", dr.read_bytes(), 0)
            if (w, h) == ICON_SIZE:
                return
        except Exception:
            pass   # malformed/short → fall through and reconvert
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  WARN  icon.png present but Pillow missing — skipping icon.dr "
              "(pip install Pillow)")
        return
    from . import img
    print(f"  ICON  icon.png -> {dr.name} ({ICON_SIZE[0]}x{ICON_SIZE[1]})")
    img.convert(png, dr, resize=ICON_SIZE)


def source_include_dir(path: Path):
    """The `include/` sibling of a source file, when it has one.

    sdk/display/gfx.c -> sdk/display/include; lib/elf/elf_parse.c ->
    lib/elf/include. Returns None otherwise.
    """
    inc = Path(path).parent / "include"
    return inc if inc.is_dir() else None


def _resolve_under(base: Path, rest: str, prefix: str) -> Path:
    """Join `rest` under `base`, refusing anything that leaves `base`.

    A prefix names a tree, so `$ROOT/../x.c` or `$ROOT//etc/x.c` is an
    authoring error, not a source outside the repository to be compiled in:
    normalised here rather than left for the compiler to open. Lexical on
    purpose — the entry need not exist yet, and the caller reports a missing
    file separately.
    """
    path = Path(os.path.normpath(base / rest))
    if path != base and not path.is_relative_to(base):
        sys.exit(f"ERROR: source entry '{prefix}{rest}' resolves to {path}, "
                 f"outside {base}")
    return path


def resolve_source(entry, app_dir: Path) -> Path:
    """Resolve one duneos.yaml `sources:` entry to a path.

    `$SDK/` is sdk/, `$ROOT/` is the repository root, anything else is
    app-relative. `$ROOT/` exists for the shared pure units under lib/, which
    belong to neither the kernel tree nor the SDK tree (ADR 043) and are
    therefore reachable through no other prefix. Both prefixes are bounded to
    the tree they name.
    """
    entry = str(entry)
    if entry.startswith("$SDK/"):
        return _resolve_under(SDK_DIR, entry[len("$SDK/"):], "$SDK/")
    if entry.startswith("$ROOT/"):
        return _resolve_under(DUNEOS_ROOT, entry[len("$ROOT/"):], "$ROOT/")
    return Path(app_dir) / entry


def resolve_sources(entries, app_dir: Path):
    """(paths, include_dirs) for a list of `sources:` entries, in order."""
    paths, includes = [], []
    for entry in entries:
        path = resolve_source(entry, app_dir)
        paths.append(path)
        inc = source_include_dir(path)
        if inc is not None and inc not in includes:
            includes.append(inc)
    return paths, includes


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
        resolved = _resolve_caps(manifest.get("capabilities", []), board_cfg,
                                 SDK_DIR,
                                 manifest.get("optional_capabilities", []))
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
        sdk_inc = source_include_dir(p)
        if sdk_inc is not None:
            extra_includes.add(sdk_inc)

    # Extra sources declared in duneos.yaml — $SDK/ and $ROOT/ prefixes, each
    # auto-picking up its include/ sibling.
    try:
        extra_paths, extra_incs = resolve_sources(manifest.get("sources", []), app_dir)
    except SystemExit as e:
        print(f"  {e}", file=sys.stderr)
        return False
    for p in extra_paths:
        if not p.exists():
            print(f"  ERROR: source not found: {p}", file=sys.stderr)
            return False
        sources.append(p)
    extra_includes.update(extra_incs)

    if not sources:
        print(f"  ERROR: no .c files in {app_dir}", file=sys.stderr)
        return False

    includes = [f"-I{SDK_INCLUDE}", f"-I{build_dir}", f"-I{app_dir}"]
    for inc in sorted(extra_includes):
        includes.append(f"-I{inc}")

    # Static stack-usage instrumentation (.su frame sizes + .ci call graph) so
    # dbt can COMPUTE the app's stack instead of trusting the manifest. Cheap,
    # compile-time only, no effect on the emitted code.
    stack_flags = ["-fstack-usage", "-fcallgraph-info=su"]

    # Compile the app + SDK sources first — this produces the .ci files the
    # stack analysis needs. The manifest (which carries the computed stack) is
    # generated and compiled afterwards, once we know the size.
    objects = []
    for src in sources:
        obj = build_dir / (src.stem + ".o")
        compile_cmd = [str(cc)] + cflags + includes + stack_flags + ["-c", str(src), "-o", str(obj)]
        print(f"  CC  {src.name}")
        try:
            run(compile_cmd)
        except SystemExit:
            return False
        objects.append(obj)

    # --- Stack auto-sizing (ADR 029): dbt owns the stack, not the manifest ---
    from .stackusage import analyze as _stack_analyze, recommend as _stack_recommend
    analysis = _stack_analyze(build_dir)
    auto, reason = _stack_recommend(analysis)
    manual = int(manifest.get("stack_size", 0))   # optional override / fallback
    if auto is not None:
        final_stack = max(auto, manual)
        if manual and manual > auto:
            print(f"  [stack] computed {auto} B (worst-case {analysis['worst']}+reserve); "
                  f"manifest overrides to {manual} B ({100*auto//manual}% needed)")
        else:
            print(f"  [stack] computed {final_stack} B "
                  f"(own-code worst-case {analysis['worst']} B + reserve)")
    else:
        # Could not size safely (recursion / no entry): keep the manual value,
        # or a safe default if none was given.
        final_stack = manual if manual else 8192
        print(f"  [stack] could not auto-size ({reason}) — using {final_stack} B")
    manifest["stack_size"] = final_stack

    # Embed the (now stack-resolved) manifest as a JSON string in its ELF section.
    # optional_capabilities is a dbt build-time hint, already consumed above;
    # the kernel has no use for it, so it does not travel in the binary.
    manifest.pop("optional_capabilities", None)
    manifest["arch"] = arch   # so the kernel can reject cross-arch binaries
    manifest_json = json.dumps(manifest, separators=(",", ":"))
    manifest_c = build_dir / "_manifest.c"
    manifest_c.write_text(
        f'__attribute__((section("{MANIFEST_SECTION}"), used))\n'
        f'const char _duneos_manifest[] = {json.dumps(manifest_json)};\n'
    )
    manifest_obj = build_dir / "_manifest.o"
    print("  CC  _manifest.c")
    try:
        run([str(cc)] + cflags + includes + ["-c", str(manifest_c), "-o", str(manifest_obj)])
    except SystemExit:
        return False
    objects.append(manifest_obj)

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
