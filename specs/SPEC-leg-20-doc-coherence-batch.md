Status: PROPOSED

# SPEC-leg-20 — Documentation consistency and versioning batch

## Context

Grouping of four milestone 3 findings, all concerning the gap between what the repository claims and
what it contains. One numbered acceptance criterion per finding.

- **LEG-20** (minor, XS) — `apps/user/g_shell/font8x8.h` and `sdk/display/include/duneos/font8x8.h`
  are identical byte for byte (md5 `a12ac3a4db3b56a50bd785a12f67899d`, 110 lines each). Any glyph fix
  applied on one side only produces divergent rendering with nothing to signal it.
- **LEG-21** (minor, XS) — the `## Build & Test Commands` section of `CLAUDE.md` (l.17-46) contains
  no test command, and the README has no Testing section. `make -C tests/host test` appears only in
  `.github/workflows/ci.yml`: CI knows how to test the project, the documentation aimed at humans and
  agents does not.
- **LEG-22** (minor, XS) — `README.md:23` states "(17 ADRs)" and `CLAUDE.md:274` "17 ADRs as of
  2026-05", while `docs/adr/` contains 40.
- **LEG-23** (minor, S) — no git tag across 213 commits, no VERSION file and no CHANGELOG, even
  though application manifests already expose a version contract (`version: "0.1.0"`,
  `required_abi_version: 3`) and `CMakeLists.txt:72` declares `project(duneos)` with no VERSION
  clause.

## Scope

Remove the font duplication, document the test commands, correct the ADR count, and put a versioning
mechanism in place.

## Acceptance criteria

1. **LEG-20** — only one definition of `font8x8` remains in the repository. `apps/user/g_shell`
   consumes the SDK display header. `g_shell` builds and its on-screen text rendering is unchanged.
2. **LEG-21** — the `## Build & Test Commands` section of `CLAUDE.md` contains the host test command
   and the Python test command, and `README.md` has a section describing how to run the tests. Every
   documented command is executable as written from a clean checkout and returns a zero exit code on
   the current commit.
3. **LEG-22** — the ADR counts in `README.md` and `CLAUDE.md` match the actual number of files in
   `docs/adr/`. The chosen wording will not go stale at the next ADR added (either it quotes no
   number, or the number is verified by an automated check).
4. **LEG-23** — `CMakeLists.txt` declares an explicit version (`project(duneos VERSION x.y.z)`), a
   `CHANGELOG.md` exists and describes at minimum the current version, and the chosen versioning
   convention is documented, including its relationship with `DUNEOS_ABI_VERSION` (currently 3): the
   document must state what an ABI increment implies for the project version.
5. None of the documentation fixes introduces a new unverifiable claim: every number or path added
   matches the actual state of the repository at the time of writing.

## Out of scope

Actually placing git tags on past history (a maintainer decision, outside a code change); rewriting
the README; a consistency review of the 40 ADRs (only the count is handled here); realigning ADR 012
(handled by LEG-17); adopting any particular changelog format.

## Risks

Criterion 3 is the most fragile of the batch: changing "17" to "40" reproduces exactly the defect it
claims to fix, since the number will go stale at the next ADR. The wording must be chosen so as to
need no maintenance, failing which this finding will resurface at the next audit.

## Open questions

Which initial version should be declared for the project, and what exact relationship with
`DUNEOS_ABI_VERSION`? The decision belongs to the maintainer and governs criterion 4.
