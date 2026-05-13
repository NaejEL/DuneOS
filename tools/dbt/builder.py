import json
import subprocess
import sys
from pathlib import Path

from .constants import APP_BUILD_DIR, APP_ELF_NAME, MANIFEST_SECTION, SDK_INCLUDE, SDK_DIR, LDFLAGS
from .manifest import load_manifest, validate_manifest
from .toolchain import build_cflags


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


def build_single(app_dir: Path, arch: str, cpu: str, tc: dict) -> bool:
    build_dir = app_dir / APP_BUILD_DIR
    build_dir.mkdir(exist_ok=True)

    try:
        manifest = load_manifest(app_dir)
        validate_manifest(manifest)
    except SystemExit as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False

    cc     = tc["cc"]
    cflags = build_cflags(arch, cpu)

    sources = sorted(app_dir.glob("*.c")) + sorted(app_dir.glob("src/*.c"))

    # Extra sources declared in duneos.yaml — supports $SDK/ prefix
    extra_sources = manifest.get("sources", [])
    extra_includes = set()
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
    manifest_json = json.dumps(manifest, separators=(",", ":"))
    manifest_c.write_text(
        f'__attribute__((section("{MANIFEST_SECTION}"), used))\n'
        f'const char _duneos_manifest[] = {json.dumps(manifest_json)};\n'
    )
    sources = [manifest_c] + list(sources)

    includes = [f"-I{SDK_INCLUDE}", f"-I{app_dir}"]
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

    elf = build_dir / APP_ELF_NAME
    link_cmd = [str(cc)] + cflags + LDFLAGS + [str(o) for o in objects] + ["-o", str(elf)]
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
