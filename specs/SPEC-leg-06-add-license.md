Status: PROPOSED

# SPEC-leg-06 — Add a licence to the repository and reference third-party ones

## Context

Finding LEG-06 (major, XS): no licence file is versioned
(`git ls-files | grep -ciE '(^|/)(licen|copying)'` returns 0), and neither `README.md` nor
`CLAUDE.md` mentions a licence. The repository is public and accepts pull requests (the most recent
being PR #2, merged).

With no explicit declaration, the code defaults to exclusive copyright: nobody may legally fork,
redistribute or contribute to it. The problem is compounded by the presence of `third_party/cjson`
(MIT) and `third_party/littlefs` (BSD-3), whose licences are referenced nowhere at the top level, as
well as the cJSON code vendored into `kernel/duneos_loader/src/`.

## Scope

Create a licence file at the repository root, mention it in the README, and enumerate the licences
of the embedded third-party dependencies.

## Acceptance criteria

1. A `LICENSE` file exists at the repository root, is tracked by git, and contains the full,
   unmodified text of a recognised open-source licence (the choice belongs to the maintainer; MIT or
   Apache-2.0 are the natural candidates given the dependency licences).
2. The file carries the copyright holder and the year.
3. `README.md` contains a section or a line naming the chosen licence and pointing to `LICENSE`.
4. A `NOTICE` file (or a dedicated README section) lists every embedded third-party dependency with
   its licence: `third_party/cjson` (MIT), `third_party/littlefs` (BSD-3-Clause), and the cJSON code
   vendored into `kernel/duneos_loader/src/`.
5. The chosen licence is compatible with those listed in criterion 4: none of the embedded
   dependencies imposes a condition the chosen licence fails to satisfy.

## Out of scope

Adding licence headers to every source file; a contributor licence agreement (CLA); legal review of
the licences of ESP-IDF components downloaded at build time; changing submodule licences.

## Risks

Choosing a licence is a maintainer decision, not a technical one: if the copyright holder has not
decided, the spec must stop and ask rather than choose on their behalf. A licence adopted and then
changed after accepting outside contributions is hard to undo.

## Open questions

Which licence should be adopted? The decision belongs to the copyright holder and governs
criterion 1.
