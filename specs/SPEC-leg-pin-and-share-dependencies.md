# LEG-06/07/09/11-16 — Pin and share what the repo depends on
Status: APPROVED

Supersedes `SPEC-leg-06-add-license.md`, `SPEC-leg-07-version-dependencies-lock.md`,
`SPEC-leg-09-pin-python-dependencies.md`, `SPEC-leg-11-repo-hygiene-batch.md`,
`SPEC-leg-15-glob-configure-depends.md`, `SPEC-leg-16-pin-idf-components.md`.
One branch, one cycle.

## Context

Nine findings, one theme: **the repo does not pin or share what it depends on** — not
its Python tools, not its IDF components, not its dev environment, not its legal
terms, not its CMake inputs. A clone on another day or another machine does not get
the same thing, and nothing records the difference.

Verified state:

- No `LICENSE`, `NOTICE`, `CONTRIBUTING.md`, or root `.gitattributes`. `README.md`
  has neither a licence nor a contributing section.
- `.gitignore:15` `*.lock`. `dependencies.lock` **is tracked** (ROADMAP:676 corrects
  the original audit; the old `SPEC-leg-07` still says otherwise). `git check-ignore`
  consults the index, so the rule is inert today and will silently swallow the next
  lock file.
- `tools/dbt.py:25` `_DEPS` — 6 bare names. `ci.yml` has **four** unpinned installs
  (`:44`, `:183`, `:225`, `:262`), plus `README.md:47`. No `requirements.txt`, no
  `pyproject.toml`. `pytest.ini` exists; there is no Python packaging.
- `.gitignore:23` `.devcontainer/`. Both `devcontainer.json` and `Dockerfile` exist
  untracked. The Dockerfile builds from `espressif/idf:${DOCKER_TAG:-latest}` while
  CI pins `v6.0.1` — **the shared environment is itself unpinned.**
- `duneos_logo.png` (1.5 MB, root) is referenced by **nothing**. The splash chain
  reads `boards/<board>/etc/splash/logo.png` and converts it at flash time
  (`flashimg.py:306-328`). `ROADMAP.md:452` claims a tracked 120×120 `.dr` derives
  from it — stale on both counts.
- `CMakeLists.txt:91` globs `arch/*/arch.cmake`, `:182` globs `firmware/blobs/*.dap`,
  neither with `CONFIGURE_DEPENDS`. `firmware/` does not exist, so the blob glob's
  stale result is the empty list — a dropped `.dap` is silently not embedded.
- `idf_component.yml:20-35` — four PHY components at `version: "*"`, gated
  `if: target == esp32`, outside the esp32s3 lock. **Not leftovers**: `boards/kincony-A16`
  is an esp32 board with `phy_addr`/RMII pins and `hal_phy.c` dispatches across all
  four. This closes the old `SPEC-leg-16`'s open question.

## Product Owner decisions

1. **Licence: LGPL-3.0-or-later.** Copyright holder `Jean Le Quellec`, year 2026.
   Chosen for a specific reason that must survive into `NOTICE`/`CONTRIBUTING`: apps
   link `libdune.a` and call the kernel through a function-pointer table. That is
   linking, not a syscall boundary — DuneOS has no Linux-style syscall exception — so
   plain GPL would make every third-party `.dap` GPL. LGPL keeps the kernel and
   libdune copyleft (modify DuneOS → publish sources to your recipient) while leaving
   an app that merely links libdune free to choose its own licence.
   No `TRADEMARK.md` (PO). Note for the record: copyleft compels publication to the
   *recipient*, never upstreaming to this project.
2. **LEG-13: delete the root master.** Keep only the board PNG, which is the build's
   real input. The master stays in git history.
3. **LEG-16: accept the lock churn.** `dependencies.lock` stays tracked and reflects
   the last target built. Manifest bounds are what protect the esp32 target.
4. **`.ps1` line endings: leave under the global LF rule.**

## Scope

- `LICENSE` (LGPL-3.0 text; LGPL is GPL-3.0 plus the LGPL additional permissions, so
  both texts as upstream requires) + `NOTICE` + a licence line in `README.md`.
  `NOTICE` lists `third_party/cjson` (MIT) and `third_party/littlefs` (BSD-3-Clause).
  cJSON is a submodule compiled in place (`duneos_loader/CMakeLists.txt:7`), **not**
  vendored under `duneos_loader/src/` as `CLAUDE.md` claims — word `NOTICE` from the
  tree, not from `CLAUDE.md`.
- `.gitignore`: drop `*.lock` and `.devcontainer/`.
- `tools/constraints.txt` (new, tracked) — pip **constraints**, `==`-pinned, one entry
  per package any site installs. Wired into `tools/dbt.py`'s install call and all four
  `ci.yml` sites via `-c`; `README.md:47` updated. *Constraints, not requirements*: the
  three consumers install different subsets, so a requirements file forces either one
  bloated install everywhere (dragging Pillow/esptool/textual into the IDF container)
  or several files, re-fragmenting the source of truth. Versions come from a `pip freeze`
  of the existing `tools/.dbt-venv/`, not from chosen values.
- `.devcontainer/` tracked, its IDF tag pinned to the CI tag.
- Root `.gitattributes`: `* text=auto eol=lf`, binary marks for image types. Must
  neither duplicate nor contradict `.knowledge/.gitattributes` (commit `909cafd`).
- `CONTRIBUTING.md` + a README pointer.
- Delete `duneos_logo.png`.
- `CONFIGURE_DEPENDS` on **both** globs. Each is single-level over a directory with a
  handful of entries, so the per-build re-glob is a few stat calls. The blob glob bites
  during normal work; the arch glob bites once per new architecture, which is exactly
  the case `CLAUDE.md`'s "zero changes to this file" invites. An explicit arch list
  would contradict that promise and is rejected.
- `idf_component.yml`: replace the four `version: "*"` with bounds derived from the
  version the solver **actually** selects for target esp32 — resolve first, then bound.
  Regenerate and commit `dependencies.lock` (editing the manifest invalidates
  `manifest_hash:55`).
- One new pytest module under `tools/dbt/tests/` for the automated criteria. Raise
  `ci.yml`'s `FLOOR` by the number of tests added.

## Acceptance criteria

Automated criteria live in `tools/dbt/tests/`, run by `python -m pytest -q`, and read
only tracked files (LEG-38).

1. `LICENSE` and `NOTICE` tracked; `README.md` names the licence and links `LICENSE`.
   *Automated.* Not automatable: that the text is the unmodified upstream LGPL-3.0.
2. `NOTICE` names both submodules with SPDX ids (`MIT`, `BSD-3-Clause`), and every
   directory under `third_party/` is mentioned by name — a future submodule fails the
   test. *Automated.*
3. `LICENSE`/`README` state the linking position: an app linking `libdune.a` is not a
   derivative work for LGPL purposes. *Automated* substring test; wording is human.
4. `git check-ignore --no-index -v dependencies.lock` exits non-zero. `--no-index` is
   required — without it the tracked file masks the live rule. *Automated.*
5. `git status --porcelain` empty at the end: dropping `*.lock` surfaced no hidden
   local file. *Builder checks and reports.*
6. `tools/constraints.txt` tracked; every entry is `name==version`. *Automated.*
7. Every package in `tools/dbt.py:_DEPS` and on every `pip install` line in `ci.yml`
   appears in `tools/constraints.txt`. *Automated.* **This is what makes LEG-09 stay
   fixed** — adding a package without pinning it fails.
8. All four `ci.yml` sites and `tools/dbt.py` pass `-c tools/constraints.txt`.
   *Automated.*
9. On a machine with no `tools/.dbt-venv/`, `python tools/dbt.py --help` creates it,
   installs and exits 0; the venv's `pip freeze` contradicts no pin. *Builder runs
   once and reports the freeze.*
10. `.devcontainer/devcontainer.json` and `Dockerfile` tracked, and the Dockerfile's
    default IDF tag equals `ci.yml`'s container tag. *Automated* — a CI bump that
    forgets the devcontainer goes red.
11. Root `.gitattributes` tracked, contains `* text=auto eol=lf`, and
    `git ls-files --eol` reports no tracked text file recorded CRLF in the index.
    *Automated.*
12. No pattern in the root `.gitattributes` targets a file governed by
    `.knowledge/.gitattributes`. *Automated.*
13. No tracked file exceeds 256 KiB. *Automated* over `git ls-files`; submodule
    contents excluded. Subsumes "reduce 5×" and stops the next 1.5 MB asset. If a
    legitimate tracked file already exceeds it, the Builder reports before choosing
    the threshold rather than adding an exception list.
14. `dbt flashimg` for `m5stack-cardputer` still produces a splash `.dr`. *Builder
    verifies locally*; not automatable in CI today.
15. `CONTRIBUTING.md` tracked, linked from `README.md`, and naming the four commands
    that exist today (`tools/duneos-bspgen.py`, `dbt flash kernel --build-only`,
    `make -C tests/host test`, `python -m pytest -q`). *Automated* — a renamed command
    breaks it. Not automatable: whether the prose helps a newcomer.
16. Both `file(GLOB)` calls carry `CONFIGURE_DEPENDS`. *Automated.*
17. After a full cardputer build, creating `arch/zz_probe/arch.cmake` and rebuilding
    re-runs CMake configuration; deleting it likewise. *Builder performs both and
    reports the reconfigure line.*
18. The cardputer build links the same object set as before, and
    `DUNEOS_KERNEL_REQUIRES` is still populated during the requirements phase.
    *CI `kernel-build` green + a diff of the two link lines.*
19. No `version: "*"` remains in any `idf_component.yml`. *Automated.*
20. Resolution succeeds for target esp32: a `kincony-A16` configure completes and
    reports concrete versions for all four PHY components. *Builder runs locally and
    pastes the resolved versions* — CI builds no esp32 board (LEG-10, out of scope).
21. After the manifest edit and regenerated lock, a cardputer build leaves
    `dependencies.lock` byte-identical to the committed one. *Builder verifies.*
22. `make -C tests/host test` and `python -m pytest -q` pass; CI green on the branch.

## Out of scope

- **LEG-10** (CI matrix, RISC-V legs) — its own cycle. Do not add an esp32 job,
  however tempting criterion 20 makes it.
- Any kernel, loader, HAL, SDK or app source change. No ABI change.
- bspgen outputs and `.knowledge/` artefacts.
- `CMAKE_SOURCE_DIR` at `CMakeLists.txt:182` — add `CONFIGURE_DEPENDS`, change nothing
  else on that line.
- Rewriting history to purge the logo blob; per-file licence headers; a CLA;
  hash-pinned Python locks; upgrading any dependency; `pyproject.toml`; rewriting the
  README beyond the pointers and the pinned install line.
- Correcting `ROADMAP.md:452` and `CLAUDE.md`'s "cJSON is vendored in
  `duneos_loader/src/`" — both stale, both doc-coherence debt. Flag in the PR body.

## Risks

- **Bounds invented rather than resolved.** Writing `^1.0.0` without resolving may
  exclude the only published version and break kincony — a board CI never compiles.
  Criterion 20 exists because no gate covers it.
- **Pins older than what is installed.** Constraints must come from the existing
  venv's freeze; an older `textual` changes TUI behaviour, an older `esptool` changes
  flashing.
- **Removing `*.lock` surfaces local files.** Criterion 5, check before committing.
- **`.gitattributes` renormalisation.** `* text=auto eol=lf` can produce a whole-tree
  diff if any tracked file is CRLF in the index. Check `git ls-files --eol` *before*
  adding the file; a non-trivial diff is a separate commit with its own review.
- **The size threshold is a tripwire on every future asset.** Intended, but the
  threshold must come from measured current sizes.
- **The pytest floor** (`ci.yml:51`) counts tests that ran; adding tests without
  raising it weakens the gate silently.
- No memory, ABI or on-device impact: nothing here changes what the CardPuter links or
  how deep it recurses, so LEG-37's on-hardware rule does not apply. If that stops
  being true, the change stops being in scope.

## Open questions

None.
