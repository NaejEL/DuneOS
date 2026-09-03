Status: PROPOSED

# SPEC-leg-07 — Stop ignoring `dependencies.lock` and tighten the `*.lock` pattern

## Context

Finding LEG-07 (major, XS): `.gitignore` line 12 contains the generic pattern `*.lock`.
`git check-ignore -v dependencies.lock` confirms the rule
(`.gitignore:12:*.lock	dependencies.lock`), and `git ls-files --error-unmatch dependencies.lock`
fails: the ESP-IDF Component Manager lockfile, present on disk, is not versioned.

Consequence: CI (container `espressif/idf:v6.0.1`) and each developer independently resolve the
contents of `managed_components/`. Two builds of the same commit may embed different versions of
TinyUSB or littlefs, and a regression introduced by a component update is invisible at review time
since no diff carries it.

The generic pattern is also a trap in waiting: it would silently hide any future
`package-lock.json`, `poetry.lock` or `Cargo.lock`.

## Scope

Replace the `*.lock` pattern with patterns targeting only genuinely local artefacts, and commit
`dependencies.lock`.

## Acceptance criteria

1. `.gitignore` no longer contains the generic `*.lock` pattern.
2. `git check-ignore -v dependencies.lock` no longer returns any matching rule (non-zero exit code).
3. `dependencies.lock` is tracked: `git ls-files --error-unmatch dependencies.lock` succeeds.
4. After these changes, `git status --porcelain` shows no build artefact that was previously ignored
   correctly: local files covered by the old pattern remain ignored through explicit rules, or it is
   established and documented that no other local `.lock` file exists in the tree.
5. A kernel build after `git clean` on a fresh machine produces the same `managed_components/`
   contents as described by the versioned lockfile.

## Out of scope

Pinning the components declared at `version: "*"` in `idf_component.yml` (handled by LEG-16);
pinning Python dependencies (handled by LEG-09); updating component versions.

## Risks

Removing a `.gitignore` pattern may surface local files that were hidden until now: criterion 4
requires checking `git status` after the change, before committing anything else.

## Open questions

None
