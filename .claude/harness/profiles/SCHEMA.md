# Profile schema

A profile is a folder `skills/_core/profiles/<name>/`: a manifest `profile.json` (strict JSON, UTF-8) and Markdown fragments in English referenced by relative path. No `.py` file appears in it. Adding a profile = adding a folder; `harness.py profile validate <folder>` returns exit 0 if the profile is valid, exit 2 with the list of errors otherwise.

## Manifest keys

- `name`: identical to the folder name. `schema_version`. `fallback`: boolean, only one profile is `true`.
- `fragments`: `{orchestrator, install, scout}`, fragment paths.
- `roles[]`: `{name, mission (fragment), read_only, tools, model_role, model_default, agent_file}`, plus `description` and `body` (fragment of the generated agent's body, RD-01); without `body`, the body is derived from `mission`. Two profiles never declare the same `agent_file`.
- `model_roles[]`, `model_presets`: model roles and presets declared by the profile (extension of MD-01 and MD-08).
- `loop`: `{max_iterations, entry_role, phases[]}`. Phase types known to the engine: `plan`, `consult`, `consolidate`, `gate`, `build`, `verify`, `interpret`, `record`. Any other type is refused.
- `artefacts[]`: `{kind, dir, glob, status_proposed, status_ready, status_done}` plus the optional `status_legacy` (`{proposed, ready, blocked, delivered}`: the former French status lines, still accepted when reading a spec). The `status_ready` values (English and legacy) form the strings forbidden in audit deliverables (FD-11).
- `verifier`: `{required_true[], issue_kinds[]}`. `deterministic_checks[]`.
- `settings`: `{deny_by_stack, deny_generic, deny_if_present}`; `gitignore[]`; `tasks`: `{inputs, tasks, test_task?}`.
- `ci`: `{script, command, max_turns, status_line, logs_dir}`. `orchestrator`: `{name, alias_of}`; `entry_keywords[]` (entry words that route to this profile).
- `gates[]`: entries in the catalog format (`id`, `kind`, `options`, `default`, `rationale`, `headless`, …). Ids stay within the namespace `<orchestrator>.`, `gate_namespaces[]` or `audit.<profile>.`.
- `journal_names[]`: journal names (JR-01).
- `params[]` (RD-03): `{name, source: detected|judgement, required}`. A `detected` parameter is computed by the core (stacks, interpreter); a `judgement` parameter is written once by the skill and recorded in the stamp (`skills.software-factory.params`). Body fragments cite them as `{{name}}` or `{{name|default value}}`. A missing required parameter yields `action: fill_params` when the upgrade is planned.
- `lens_sets[]`: `{name, lenses[], plan, deliverable_section, spec_prefix, cap_option, cap, cap_default, statuses[], gates[], fragment, map_fragment?, intro_fragment?, validator_fragment?}`.
- `lenses[]`: `name`, `prefix`, `set`, `fragment`, and the LN properties: `debt`, `shape`, `ordering`, `axis`, `axis_rank`, `unauditable`, `no_run`, `no_protocol`, `allowed_exec`, `journal_check`, `nature`, `size_scale`, `critical_definition`, `subagent_type`, `model_role`, `stack_agnostic`, `dedup_key`, `evidence_kinds`, `receives`, `validator_fields`, `deliverable`.
- `detection`: `{accept, reject, criteria[]}`; a criterion carries `{id, kind, weight, …}`, `kind` ∈ `glob`, `dir`, `dependency`, `content`, `judgement`. Score = sum of the weights found; `retained` if score ≥ `accept`, `rejected` if score < `reject`, otherwise `uncertain`.
- `audit_sections[]`: sections of the profile's `AUDIT.md` (templates under `templates/`).

## Fragments

`roles/<role>.md`, `bodies/<role>.md` (rendered body of the agents and the orchestrator), `lenses/<lens>.md` (fragments starting with `_` are shared by a lens set), `orchestrator.md`, `install.md`, `scout.md`.
