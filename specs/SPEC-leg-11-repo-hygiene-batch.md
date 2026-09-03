Status: PROPOSED

# SPEC-leg-11 — Repository hygiene and contributor onboarding batch

## Context

Grouping of four minor XS findings, all from the hygiene family and all assigned to milestone 2.
They share the same nature (getting the repository ready for an outside contributor) and do not
overlap, hence a common spec with one acceptance criterion per finding.

- **LEG-11** (minor, XS) — `.gitignore` l.20 contains `.devcontainer/`. The directory exists on disk
  with `devcontainer.json` and a `Dockerfile`, but is not versioned: the reproducible environment
  recipe stays on the maintainer's machine and can silently diverge from the image CI uses.
- **LEG-12** (minor, XS) — no `.gitattributes`, even though the repository ships `ci/factory.ps1` and
  therefore targets Windows contributors. No CRLF file is tracked today: the risk is latent, not yet
  realised.
- **LEG-13** (minor, XS) — `duneos_logo.png` (1,547,148 bytes, 1024×1024 RGBA PNG) is the largest
  first-party file in the repository, while only a 120×120 derivative is actually embedded.
- **LEG-14** (minor, XS) — no CONTRIBUTING file, and no "Contributing" section in `README.md` or
  `CLAUDE.md`, on a public repository that has already accepted a pull request.

## Scope

Version the development environment, normalise line endings, shrink the heaviest asset, and document
the path for an outside contributor.

## Acceptance criteria

1. **LEG-11** — `.devcontainer/devcontainer.json` and `.devcontainer/Dockerfile` are tracked
   (`git ls-files` lists them); the `.devcontainer/` rule is gone from `.gitignore` or narrowed to
   local artefacts only. The ESP-IDF version in the `Dockerfile` matches the container used by
   `.github/workflows/ci.yml` (`espressif/idf:v6.0.1`), or the discrepancy is documented.
2. **LEG-12** — a `.gitattributes` exists at the root, is tracked, and defines at minimum
   `* text=auto eol=lf`, CRLF handling for `*.ps1`, and binary marking for `*.png`. After it is
   added, `git ls-files --eol` reports no tracked text file in CRLF.
3. **LEG-13** — the tracked logo asset's size is reduced by at least a factor of 5 from the current
   1,547,148 bytes, without losing the ability to regenerate the embedded 120×120 asset; the command
   generating the `.dr` from the source is documented.
4. **LEG-14** — a `CONTRIBUTING.md` file exists at the root, is tracked, and covers at minimum:
   environment setup (`.duneos_board`, `duneos-bspgen.py`), the build command, the test command, and
   the commit message convention. `README.md` points to it.
5. None of the four changes causes a build artefact to appear in `git status --porcelain`.

## Out of scope

Rewriting history to purge the old logo (the historical weight stays in the repository; that is
accepted); adding a CLA; reworking the README; documenting test commands in CLAUDE.md (handled by
LEG-21).

## Risks

Criterion 3 cannot be satisfied by simply deleting the logo: the 120×120 asset generation chain must
be verified to still work from the reduced source. Criterion 2 may produce a massive diff if CRLF
files existed undetected — check the diff size before committing.

## Open questions

None
