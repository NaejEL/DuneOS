---
name: factory-run
description: Runs one software-factory cycle (Planner → human gate on the spec → Builder → Verifier with a fix loop) on a requirement or an approved spec.
disable-model-invocation: true
argument-hint: "<requirement | path to an approved spec>"
---

Drive a full cycle of the DuneOS software factory. Three roles with separate contexts: `factory-planner` (read-only), `factory-builder` (implements), `factory-verifier` (adversarial, modifies nothing). The user is the Product Owner: no build starts without a spec they approved.

## 1. Input

Examine the argument given:

- If it is the path of an **existing** file under `specs/` whose status line (first non-title line of the file) is exactly `Status: APPROVED` → go straight to step 3 (Build).
- If the file exists but the status is not `APPROVED` → treat it as a spec to re-present: resume at step 2, sub-step "presentation to the user".
- Otherwise → treat the argument as a natural-language **requirement**, step 2.
- Empty argument → ask the user for the requirement (AskUserQuestion); in headless mode, fail immediately with the step 2 message.

## 2. Plan phase — mandatory human gate

**If no user interaction is possible (headless run, `claude -p`), fail immediately with this message and a non-zero exit: "CI cycle: provide the path of an already APPROVED spec under specs/. The Plan phase requires an interactive session." Never build without an approved spec.**

1. Launch the `factory-planner` subagent (Agent, `subagent_type: "factory-planner"`, `run_in_background: false`) with the requirement verbatim and this instruction: produce the draft spec in the format imposed by its definition (Context / Scope / Acceptance criteria / Out of scope / Risks / Open questions).
2. On its return: if the *Open questions* section is not "None", put each question to the user via AskUserQuestion. Fold the answers into the spec (context, scope or criteria depending on the nature of the answer).
3. Write the spec to `specs/SPEC-<requirement-slug>.md` (slug: lowercase, requirement keywords joined by hyphens, short). First line after the title: `Status: PROPOSED`. Create the `specs/` directory if absent.
4. Present the spec to the user (summary + acceptance criteria in full) and ask for their **explicit approval** (AskUserQuestion: Approve / Change).
   - Approval → replace `Status: PROPOSED` with `Status: APPROVED` in the file, go to step 3.
   - Change request → fold in the feedback, rewrite the file, re-present. Iterate until approval or explicit abandonment by the user.

## 3. Build phase

Launch the `factory-builder` subagent (fresh context, `run_in_background: false`) with, as input: the path of the approved spec and the instruction to implement it in full, with build and tests passing before handing back. Remind it of the environment: ESP-IDF/dbt builds through PowerShell (`idf.py build`, `python tools/dbt.py buildall`), Python tests through `python -m pytest tools/dbt/tests -q`.

## 4. Verify phase — fix loop, 3 iterations maximum

Initialise `iteration = 1`.

1. Launch a **fresh** `factory-verifier` subagent (blank context, independent of the Builder, `run_in_background: false`) with the spec path. Collect the final JSON block of its answer (`verdict`, `tests_passed`, `issues`).
2. If `verdict == "APPROVED"` **and** `tests_passed == true` → success, leave the loop, go to the final report.
3. Otherwise: if `iteration == 3`, **fail explicitly** — publish the complete list of remaining issues in the final report with FAILED status. Never approve by exhaustion.
4. Otherwise: relaunch `factory-builder` with (a) the spec path, (b) the **complete list of the Verifier's `issues`** — file, severity, description, verbatim — and (c) the instruction: fix every issue without regressing on the criteria already satisfied, with build and tests passing before handing back. Then `iteration += 1` and back to sub-step 1 with a **new** Verifier.

## 5. Final report

Present to the user, as text (no report file):

- files modified: output of `git status --short`;
- build/test results (commands run and exit codes reported by the last Verifier);
- the Verifier's final verdict and the number of Build↔Verify iterations consumed;
- suggested next action: review of the diff by the user (`git diff`), then a commit **by them** — never commit on their behalf.

On failure (headless gate, 3 iterations without APPROVED, or user abandonment), the report states it clearly with the remaining issues, and the session ends in error.
