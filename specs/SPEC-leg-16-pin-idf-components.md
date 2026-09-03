Status: PROPOSED

# SPEC-leg-16 — Pin the ESP-IDF components declared at `version: "*"`

## Context

Finding LEG-16 (minor, S): `kernel/duneos_kernel/idf_component.yml` lines 20 to 35 declares four
Ethernet PHY drivers — `espressif/lan87xx`, `espressif/ksz80xx`, `espressif/rtl8201`,
`espressif/ip101` — at `version: "*"`, gated by `if: target == esp32`.

The `dependencies.lock` lockfile is generated for `target: esp32s3` (line 56) and therefore does not
contain those components. A build for the `esp32` target resolves these four packages with no version
bound and no hash: it fetches the latest published version, different depending on the date, and
nothing records it.

This finding depends on LEG-07: as long as `dependencies.lock` is not versioned, pinning these
components brings nothing verifiable.

## Scope

Replace the `version: "*"` constraints with bounded constraints, and ensure resolution for the
`esp32` target is reproducible.

## Acceptance criteria

1. No component in `kernel/duneos_kernel/idf_component.yml` is declared at `version: "*"`: every
   entry carries a bounded constraint (at minimum an upper bound on the major version).
2. The chosen constraints correspond to genuinely published, resolvable versions: dependency
   resolution completes without error for the `esp32` target.
3. A lockfile covering the `esp32` target is produced and versioned, or the absence of `esp32`
   coverage is explicitly documented with its reason (for example: no `esp32` board is supported
   today).
4. The build for `m5stack-cardputer` (target `esp32s3`) is unchanged: the contents of
   `dependencies.lock` for that target do not vary because of this change.
5. The four components remain gated by `if: target == esp32`: they are not downloaded during an
   `esp32s3` build.

## Out of scope

Updating these components to newer versions; actual support for an `esp32` board; pinning Python
dependencies (handled by LEG-09); adding hash verification.

## Risks

No `esp32` board is present in `boards/` today: these declarations could be a leftover. If the check
shows no `esp32` target is supported, the most honest fix may be to remove the four entries rather
than pin them — criterion 3 leaves that door open, but the decision must be taken and written down,
not merely endured.

## Open questions

Do these four PHY drivers correspond to a genuinely intended `esp32` target, or are they a leftover
to remove?
