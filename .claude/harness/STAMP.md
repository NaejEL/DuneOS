# The `.claude/harness.json` stamp

**Versioned** file of each equipped project (no machine-specific data: no absolute path, no `rg_path` key, `harness.py stamp validate` refuses them). Strict JSON, 2-space indentation, atomic write. Schema `schema_version: 1`.

| Key | Content |
| --- | --- |
| `schema_version` | `1`. Any other value is refused. |
| `autonomy` | `gate` or `auto`. Set to `auto` if absent; an existing value is **never** modified by installation or upgrade. |
| `profiles[]` | `{name, added, by}`, append-only. |
| `skills` | One object per installing skill: `software-factory` and/or `code-index` (`ai-lab` is the `research` profile of `software-factory`). |
| `migrations[]` | `{id, at, sources: [{path, sha256}]}`, append-only (run registries already published). |
| `inferred_from` | Optional: estimated generation of the project without a stamp (`pre-core`, `core-<v>`) created by the first upgrade. |

## `skills.<skill>`

- `repo_commit`, `repo_describe`, `repo_dirty`: version of the claude-skills repository that produced the installation (`harness.py version`).
- `core_version` (software-factory) or `tool_version` (code-index, rgkb's `TOOL_VERSION`).
- `installed_at` (set on first write), `updated_at` (updated by an apply that changes something): ISO 8601, local time with offset, to the second.
- `options`: software-factory `stacks`, `interpreter`, `ci`, `code_index_offered`; code-index `no_wire`, `hooks`, `interpreter`.
- `params` (software-factory): the profiles' `judgement` parameters, written once.
- `contributes` (code-index): the tools this skill adds to the agents and CI scripts.
- `files`: `{"<relative path>": {kind: generated|merged|copied|legacy, blocks: {"<block>": "<sha256>"}}}`.

## Rules

- A Phase 2 stamp (`profiles` alone) moves to schema 1: all its keys are kept, in their order, the new ones come after.
- An apply with no change does not touch the stamp (byte-for-byte identical); a stop before the end leaves it unchanged, it is written last.
- A `legacy` file is a generated file kept as is at the user's request (gate `upgrade.convert`): the upgrade does not modify it, the doctor flags it.
