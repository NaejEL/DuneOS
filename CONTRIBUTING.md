# Contributing to DuneOS

## Licence

DuneOS is LGPL-3.0-or-later. By contributing you agree your work ships under
those terms. There is no CLA and no copyright assignment.

The choice rests on the ABI being a linking boundary, not a syscall one, so it
constrains anyone changing that boundary. `NOTICE` carries the reasoning — read
it before you touch the ABI, and keep it true if the boundary moves.

## Setting up

Pick a board and generate its support files. The four generated files
(`board_config.h`, `sdkconfig.board`, `partitions.csv`, `idf_target.txt`) are
never hand-edited — edit `boards/<board>/board.yaml` and re-run:

```bash
echo m5stack-cardputer > .duneos_board
python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml
```

Python dependencies are pinned in `tools/constraints.txt`. `tools/dbt.py`
bootstraps its own venv from it on first run; if you install by hand, pass
`-c tools/constraints.txt`. Adding a package means adding its pin in the same
commit — a test enforces it.

The `.devcontainer/` image is the reference environment, pinned to the same
ESP-IDF tag as CI.

## Before you open a pull request

Run all four. Each covers something the others do not, and CI runs every one.

```bash
python tools/dbt.py flash kernel --build-only   # kernel builds, no new warning
make -C tests/host test                         # C host suites
python -m pytest -q                             # dbt and bspgen tooling
python tools/dbt.py qemu --board esp32s3-qemu --build-dir build-esp32s3-qemu
                                                # hardware-free boot + loader
```

The qemu leg needs no *physical* board, but it does need `.duneos_board` to say
`esp32s3-qemu` — on a cardputer checkout the command above exits 6 rather than
running. Switch the board (and full-clean, `sdkconfig` is board-specific), run
it, switch back. It is the only check that actually boots the kernel and loads
a `.dap`, so skipping it means CI finds the breakage instead of you.

Use `dbt`, not raw `idf.py`, outside the IDF container: it resolves the pinned
toolchain for you.

## House rules

- Kernel is C17. Errors are `int`: 0 on success, `-errno` on failure (ADR 001).
- Comments explain a non-obvious *why*, never what the code does.
- Any change to an exported symbol or an ABI struct layout bumps
  `DUNEOS_ABI_VERSION` in `abi.h`.
- A test builds what it asserts against. Reading a generated file makes it pass
  only on the machine that generated it; the read guard in `conftest.py` will
  fail you for it.
- A change that alters what a physical board links, or how deep it recurses, is
  tested on that board before it reaches `main`.

`CLAUDE.md` carries the long form: conventions, architecture, and the
hard-won lessons behind these rules.
