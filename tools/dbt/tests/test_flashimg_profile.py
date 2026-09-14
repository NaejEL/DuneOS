"""LEG-36 — the image is built from a profile, and it is sized before it is written.

Every fixture is constructed under tmp_path: partitions.csv and app ELFs are
gitignored, and the root conftest read guard fails any test that opens them.
"""

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt import flashimg, system  # noqa: E402
from dbt import manifest as manifest_mod  # noqa: E402
from dbt.qemu import QEMU_BOARDS  # noqa: E402

DBT = REPO_ROOT / "tools" / "dbt.py"

BOARD = "tb-devkit"
PARTITIONS = (
    "# Name,   Type, SubType, Offset,   Size\n"
    "nvs,      data, nvs,     0x9000,   0x6000\n"
    "factory,  app,  factory, 0x10000,  0x180000\n"
    "sysbin,   data, spiffs,  0x190000, {size}\n"
)


def _board_tree(tmp_path: Path, size: str | None) -> Path:
    board_dir = tmp_path / "boards" / BOARD
    board_dir.mkdir(parents=True)
    if size is None:
        rows = [r for r in PARTITIONS.splitlines() if not r.startswith("sysbin")]
        board_dir.joinpath("partitions.csv").write_text("\n".join(rows) + "\n")
    else:
        board_dir.joinpath("partitions.csv").write_text(PARTITIONS.format(size=size))
    return board_dir


def _fake_apps(monkeypatch, tmp_path: Path, apps: dict[str, int]) -> None:
    """Install a synthetic app tree: {app_name: elf size in bytes}."""
    entries = []
    for name, size in apps.items():
        app_dir = tmp_path / "apps" / name
        (app_dir / "build").mkdir(parents=True)
        (app_dir / "build" / "app.elf").write_bytes(b"\x7fELF" + b"\0" * (size - 4))
        entries.append((app_dir, True))
    monkeypatch.setattr(flashimg, "find_apps", lambda: entries)
    monkeypatch.setattr(manifest_mod, "load_manifest",
                        lambda app_dir: {"name": app_dir.name})


def _rooted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(flashimg, "DUNEOS_ROOT", tmp_path)
    monkeypatch.setattr(system, "DUNEOS_ROOT", tmp_path)


def _profile(name: str, apps: list[str]) -> dict:
    return {"name": name, "board": BOARD, "apps_flash": apps, "init_flash": []}


def _stub_image(staging_dir, out_path, *_a, **_kw):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\0")


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# --- criterion 1 -----------------------------------------------------------

@pytest.mark.parametrize("argv", [["flash", "sysbin"], ["flashimg"]])
def test_deleted_verb_refused_by_name(argv):
    proc = subprocess.run([sys.executable, str(DBT), *argv],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode != 0
    assert "dbt system flash" in proc.stdout + proc.stderr


def test_flash_help_no_longer_lists_sysbin():
    proc = subprocess.run([sys.executable, str(DBT), "flash", "--help"],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0
    assert "sysbin" not in proc.stdout


# --- criterion 2 -----------------------------------------------------------

def test_stage_profile_has_no_default():
    param = inspect.signature(flashimg._stage).parameters["profile"]
    assert param.default is inspect.Parameter.empty


def test_stage_rejects_none_profile(tmp_path):
    with pytest.raises(ValueError):
        flashimg._stage(tmp_path, BOARD, None)


# --- criterion 3 -----------------------------------------------------------

def test_system_flash_reaches_stage_with_a_profile(monkeypatch, tmp_path):
    from dbt import cli

    seen = {}
    _rooted(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "DUNEOS_ROOT", tmp_path)
    monkeypatch.setattr(flashimg, "_PORT_FILE", tmp_path / ".duneos_port")
    monkeypatch.delenv("DUNEOS_PORT", raising=False)
    (tmp_path / ".duneos_board").write_text("m5stack-cardputer\n")
    monkeypatch.setattr(flashimg, "_get_board_name", lambda: "m5stack-cardputer")
    monkeypatch.setattr(flashimg, "_stage",
                        lambda d, b, p, **kw: seen.setdefault("profile", p) and 0)
    monkeypatch.setattr(flashimg, "_get_sysbin_size", lambda b: 1 << 20)
    monkeypatch.setattr(flashimg, "_create_image", _stub_image)
    cli.cmd_system_flash(_Args(profile="cardputer-recovery", no_build=True,
                               port=None, baud=460800))
    assert isinstance(seen["profile"], dict)
    assert seen["profile"]["apps_flash"] == ["usb_shell"]


def test_qemu_build_sysbin_reaches_stage_with_a_profile(monkeypatch, tmp_path):
    from dbt import qemu

    seen = {}
    monkeypatch.setattr(flashimg, "_stage",
                        lambda d, b, p, **kw: seen.setdefault("profile", p) and 0)
    monkeypatch.setattr(flashimg, "_get_sysbin_size", lambda b: 1 << 20)
    monkeypatch.setattr(flashimg, "_create_image", _stub_image)
    qemu._build_sysbin("esp32s3-qemu", tmp_path)
    assert isinstance(seen["profile"], dict)
    assert seen["profile"]["apps_flash"] == [qemu.SMOKE_APP]


# --- criterion 4 -----------------------------------------------------------

def test_init_yaml_safe_is_staged(monkeypatch, tmp_path):
    _rooted(monkeypatch, tmp_path)
    _fake_apps(monkeypatch, tmp_path, {"usb_shell": 128})
    staging = tmp_path / "staging"
    flashimg._stage(staging, BOARD, _profile("p", ["usb_shell"]))
    safe = (staging / "init.yaml.safe").read_text()
    assert "/bin/usb_shell.dap" in safe
    assert "restart: always" in safe


# --- criterion 5 -----------------------------------------------------------

def test_recovery_profile_stages_exactly_one_app(monkeypatch, tmp_path):
    profile = system.load_profile("cardputer-recovery")
    referenced = {e["path"].removeprefix("/bin/").removesuffix(".dap")
                  for e in profile["init_flash"]}
    assert referenced <= set(profile["apps_flash"])

    _rooted(monkeypatch, tmp_path)
    _fake_apps(monkeypatch, tmp_path,
               {"usb_shell": 128, "launcher": 128, "snake": 128})
    staging = tmp_path / "staging"
    n = flashimg._stage(staging, profile["board"], profile)
    assert n == 1
    assert [p.name for p in (staging / "bin").iterdir()] == ["usb_shell.dap"]
    assert "/bin/usb_shell.dap" in (staging / "init.yaml").read_text()


# --- criterion 6 -----------------------------------------------------------

def test_every_board_with_an_init_yaml_has_a_profile():
    boards = {p.parent.name for p in (REPO_ROOT / "boards").glob("*/init.yaml")}
    boards -= set(QEMU_BOARDS)
    covered = set()
    for prof in (REPO_ROOT / "profiles").glob("*/profile.yaml"):
        for line in prof.read_text().splitlines():
            if line.startswith("board:"):
                covered.add(line.split(":", 1)[1].strip())
    assert boards <= covered, f"no profile targets {sorted(boards - covered)}"


# --- criteria 7, 8, 9, 10 --------------------------------------------------

def _build_image(monkeypatch, tmp_path, apps, sysbin="0x10000", board=BOARD):
    _rooted(monkeypatch, tmp_path)
    _board_tree(tmp_path, sysbin)
    _fake_apps(monkeypatch, tmp_path, apps)
    flashimg.cmd_flashimg(_Args(board=board, profile=_profile("tb-p", list(apps)),
                                out_dir=tmp_path / "out", image_only=True))
    return tmp_path / "out" / "sysbin.bin"


def _sizes(message: str) -> tuple[int, int]:
    partition = int(re.search(r"partition: (\d+) bytes", message).group(1))
    staged    = int(re.search(r"staged: +(\d+) bytes", message).group(1))
    return partition, staged


def test_overflow_is_a_named_error_not_a_traceback(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _build_image(monkeypatch, tmp_path, {"big": 200 * 1024}, sysbin="0x10000")
    message = str(exc.value)
    out = capsys.readouterr().out + message
    partition, staged = _sizes(message)
    assert partition == 0x10000 and staged > partition
    assert staged >= 200 * 1024
    assert "tb-p" in message and BOARD in message
    assert "LittleFSError" not in out


def test_residual_littlefs_enospc_is_the_same_named_error(monkeypatch, tmp_path,
                                                         capsys):
    import littlefs

    class _FullFS:
        def __init__(self, **kw):
            self.context = type("C", (), {"buffer": b""})()

        def mkdir(self, _path):
            pass

        def open(self, *_a, **_kw):
            raise littlefs.LittleFSError(
                littlefs.LittleFSError.Error.LFS_ERR_NOSPC)

    monkeypatch.setattr(littlefs, "LittleFS", _FullFS)
    with pytest.raises(SystemExit) as exc:
        _build_image(monkeypatch, tmp_path, {"small": 1024}, sysbin="0x10000")
    message = str(exc.value)
    out = capsys.readouterr().out + message
    partition, staged = _sizes(message)
    assert partition == 0x10000 and staged < partition
    assert "tb-p" in message and BOARD in message
    assert "metadata" in message
    assert "LittleFSError" not in out


def test_image_size_comes_from_partitions_csv(monkeypatch, tmp_path):
    image = _build_image(monkeypatch, tmp_path, {"small": 1024}, sysbin="0x40000")
    assert image.stat().st_size == 0x40000


def test_no_sysbin_size_literal_left_in_flashimg():
    source = Path(flashimg.__file__).read_text()
    assert "0x100000" not in source
    assert "0x190000" not in source


def test_board_without_a_sysbin_partition_is_refused_by_name(monkeypatch, tmp_path):
    _rooted(monkeypatch, tmp_path)
    _board_tree(tmp_path, None)
    with pytest.raises(SystemExit) as exc:
        flashimg._get_sysbin_size(BOARD)
    assert BOARD in str(exc.value) and "sysbin" in str(exc.value)


# --- criterion 11 ----------------------------------------------------------

def test_duplicate_app_is_counted_once(monkeypatch):
    monkeypatch.setattr(system, "app_elf_size", lambda name: 1000)
    monkeypatch.setattr(system, "parse_partition_sizes", lambda board: {"sysbin": 4096})
    info = system.compute_image_sizes(
        {"name": "p", "board": BOARD, "apps_flash": ["tail", "cat", "tail"]})
    assert [n for n, _ in info["per_app_flash"]].count("tail") == 1
    assert info["flash_total"] == 2000


def test_check_profile_warns_on_a_duplicate(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(system, "DUNEOS_ROOT", tmp_path)
    (tmp_path / "boards" / BOARD).mkdir(parents=True)
    monkeypatch.setattr(system, "_read_sdkconfig", lambda board: [])
    monkeypatch.setattr(system, "_app_map", lambda: {"tail": tmp_path, "cat": tmp_path})
    monkeypatch.setattr(system, "load_manifest", lambda p: {"permissions": 0})
    monkeypatch.setattr(system, "validate_manifest", lambda m: None)
    monkeypatch.setattr(system, "check_app", lambda *a: [])
    rc = system.check_profile(
        {"name": "p", "board": BOARD, "apps_flash": ["tail", "cat", "tail"]})
    out = capsys.readouterr().out
    assert rc == 0
    assert "more than once" in out and "tail" in out
    assert "(2 apps in /bin" in out


def test_contest_profile_lists_each_app_once():
    profile = system.load_profile("cardputer-contest")
    assert system.duplicates(profile["apps_flash"]) == []


# --- criterion 19 ----------------------------------------------------------

def test_no_tracked_doc_names_a_deleted_verb():
    # docs/adr/* and the ROADMAP's historical Phase-25 checkboxes are excluded
    # deliberately: they record what was true when they were written.
    targets = [REPO_ROOT / "README.md", REPO_ROOT / "CLAUDE.md",
               REPO_ROOT / "CONTRIBUTING.md", REPO_ROOT / "docs" / "testing.md"]
    targets += sorted((REPO_ROOT / "profiles").glob("*/profile.yaml"))
    hits = []
    for path in targets:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if any(v in line for v in ("flash sysbin", "dbt.py flashimg",
                                       "dbt flashimg")):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "deleted verbs still documented:\n" + "\n".join(hits)


# One CSV row, one integer syntax. The offset and the size used to be read by
# two parsers that disagreed: int(x, 0) for the offset, shorthand-aware for the
# size. ESP-IDF accepts `1M`/`256K` in both columns.
@pytest.mark.parametrize("text,expected", [
    ("0x190000", 0x190000), ("1048576", 1048576),
    ("1M", 1024 * 1024), ("256K", 256 * 1024),
    ("1MB", 1024 * 1024), (" 64K ", 64 * 1024),
    ("", None), ("garbage", None), ("1G", None),
])
def test_partition_integers_have_one_syntax(text, expected):
    from dbt.system import parse_csv_int
    assert parse_csv_int(text) == expected


def test_a_shorthand_offset_is_read_not_crashed_on(tmp_path, monkeypatch):
    board = tmp_path / "boards" / "b"
    board.mkdir(parents=True)
    (board / "partitions.csv").write_text(
        "# Name, Type, SubType, Offset, Size\n"
        "factory, app,  factory, 0x10000, 1M\n"
        "sysbin,  data, spiffs,  1M,      256K\n"
    )
    from dbt import flashimg, system
    monkeypatch.setattr(flashimg, "DUNEOS_ROOT", tmp_path)
    monkeypatch.setattr(system, "DUNEOS_ROOT", tmp_path)
    assert flashimg._sysbin_row("b") == (1024 * 1024, 256 * 1024)
