# ADR 006 — Manifest extensibility: unknown fields ignored, additive changes don't bump ABI

**Status:** Accepted · 2026-05-19

## Context

DuneOS apps carry a JSON manifest embedded in a `.duneos_manifest` ELF section, parsed at load time. Current fields: `name`, `version`, `required_abi_version`, `permissions`, `stack_size`, `heap_size`, `arch`, `priority`, `sources`. The loader rejects an app whose `required_abi_version > DUNEOS_ABI_VERSION`.

As phases progress, new fields will appear (`watchdog_ms`, `wake_lock`, `core_affinity`, `sandbox_paths`, …). Two failure modes to design against:

1. **Old kernel, new manifest.** A `.dap` built today with a `priority` field, deployed on a kernel from before priorities existed. Should the kernel reject the app or ignore the unknown field?
2. **New kernel, old manifest.** A `.dap` from Phase 22 (no `arch` field), loaded by a Phase 24 kernel that expects `arch`. The kernel must continue to load it (with a sane default), not refuse.

The decision needs a clear, documented rule because the two cases are bidirectional and a permissive answer for one is the strict answer for the other.

## Decision

**Forward compatibility (new field, old kernel):**
- Unknown JSON fields in the manifest are **silently ignored** by the loader. No warning, no error.
- Apps must not rely on a new field to be honoured by older kernels; they fall back to defaults if the kernel doesn't know about the field.

**Backward compatibility (old manifest, new kernel):**
- Missing fields use documented defaults. Each field's ADR or documentation MUST specify its default value.
- Currently: `permissions: 0`, `stack_size: 8192`, `heap_size: 8192`, `arch: <ignored, kernel skips ISA check>`, `priority: normal` ([[ADR-004]]).

**ABI versioning rule:**

| Change | ABI bump required? |
|---|---|
| Add a new optional field | **No** |
| Add a new required field | **Yes** (and the old kernel can't run new apps anyway because `required_abi_version` rises) |
| Remove a field | **Yes** |
| Change the semantics of an existing field (e.g. `stack_size` from bytes to KiB) | **Yes** |
| Rename a field | **Yes** (semantically a remove + add) |
| Change the type/range of a field's value | **Yes** |

The current ABI is v3 (Phase 22 — typed dispatch table). Manifest extensibility changes do not on their own justify an ABI bump; the bump is tied to symbol table or `duneos_api_t` layout changes per [[ADR-???]] (no ABI-stability ADR exists yet — `abi.h` is the source of truth).

**Documenting standard fields:** every field documented in `kernel/duneos_kernel/include/duneos/abi.h` (the manifest struct) and listed in README.md / CLAUDE.md. Adding a field = adding a doc line. Field with no doc = should never have been added.

## Consequences

- Phase 25 (`dbt system check`) and the kernel loader share the same rule, eliminating one class of "validated by dbt but rejected by kernel" bugs.
- Apps stay future-compatible: a `priority: high` line is safe to write today, even if the current kernel ignores it — it just runs at normal until the kernel catches up.
- The cost is that typos in manifests are silently ignored (`stak_size: 16384` → kernel uses default 8192, app stack-overflows). Mitigation: `dbt build` validates manifest fields against the known schema at compile time and warns on unknowns. The *kernel* stays permissive; the *tool* catches typos.
- An ABI bump remains a heavy event reserved for symbol/struct changes. Manifest evolution is decoupled from it.

## Alternatives

- **Strict (unknown field = reject)** — rejected. Breaks forward compatibility; every new field would force a kernel update on all deployed boards before any new app can ship.
- **Schema versioning (`manifest_version: N`)** — rejected as redundant. `required_abi_version` already serves this role for hard incompatibilities; for soft ones, the field-presence model is simpler.
- **Bump ABI on every field addition** — rejected. Devalues the ABI version as a signal; nothing breaks when a field is added optionally.
