# ai-lab installation rules (AL-01 to AL-09) and the lab-run state machine (LR-01 to LR-09).

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import helpers
from hcore import fusion, gates, journal, labrun, lab, models, verdicts


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def read(path: Path) -> str:
    with open(str(path), encoding="utf-8", newline="") as fh:
        return fh.read()


class InstallTest(helpers.TempDirCase):
    def test_al01_deny_python_generic_and_present_data_only(self):
        """AL-01: Python and generic rules always; data, weights and caches only when present."""
        self.assertNotIn("Read(./data/**)", lab.deny_rules(self.tmp))
        (self.tmp / "data").mkdir()
        (self.tmp / "wandb").mkdir()
        write(self.tmp / "src" / "model.safetensors", "x")
        write(self.tmp / "src" / ".cache" / "c.bin", "x")
        rules = lab.deny_rules(self.tmp)
        for rule in lab.PYTHON_DENY + lab.GENERIC_DENY:
            self.assertIn(rule, rules)
        for rule in ("Read(./data/**)", "Read(./wandb/**)", "Read(./**/*.safetensors)", "Read(./**/.cache/**)"):
            self.assertIn(rule, rules)
        for absent in ("Read(./checkpoints/**)", "Read(./models/**)", "Read(./**/*.pt)", "Read(./runs/**)"):
            self.assertNotIn(absent, rules)
        self.assertEqual(len(rules), len(set(rules)))

    def test_al02_al03_al04_apply_created_then_identical(self):
        """AL-02, AL-03, AL-04: tasks, deny rules and gitignore lines; a second application changes nothing."""
        states = lab.apply(self.tmp, "py -3 -m pytest -q", "py -3 -m pytest -q")
        self.assertEqual(set(states.values()), {fusion.CREATED})
        tasks = json.loads(read(self.tmp / ".vscode" / "tasks.json"))
        self.assertEqual([i["id"] for i in tasks["inputs"]], ["labProtocol"])
        self.assertEqual([t["label"] for t in tasks["tasks"]],
                         ["Lab: run headless", "Lab: run headless (YOLO)", "Lab: tests unitaires",
                          "Lab: nouvelle expérience (session)"])
        self.assertEqual(tasks["tasks"][0]["windows"]["command"], "pwsh -File ci/lab.ps1 ${input:labProtocol}")
        self.assertEqual(tasks["tasks"][1]["windows"]["command"], "pwsh -File ci/lab.ps1 ${input:labProtocol} -Yolo")
        self.assertEqual(read(self.tmp / ".gitignore"), "lab-logs/\nexperiments/results/\n.harness/runs/\n.harness/tmp/\n")
        snapshot = {p.name: read(p) for p in self.tmp.rglob("*") if p.is_file()}
        self.assertEqual(set(lab.apply(self.tmp, "py -3 -m pytest -q", "py -3 -m pytest -q").values()), {fusion.IDENTICAL})
        self.assertEqual(snapshot, {p.name: read(p) for p in self.tmp.rglob("*") if p.is_file()})

    def test_al02_existing_settings_and_gitignore_are_completed_not_replaced(self):
        """AL-01, AL-03: existing keys, comments and lines are preserved; the missing ones are added."""
        write(self.tmp / ".claude" / "settings.json", '{\n  // mine\n  "permissions": {"deny": ["Read(./.venv/**)"]}\n}\n')
        write(self.tmp / ".gitignore", "node_modules\nlab-logs/\n")
        lab.apply(self.tmp)
        settings = read(self.tmp / ".claude" / "settings.json")
        self.assertIn("// mine", settings)
        self.assertEqual(settings.count("Read(./.venv/**)"), 1)
        self.assertEqual(read(self.tmp / ".gitignore"), "node_modules\nlab-logs/\nexperiments/results/\n.harness/runs/\n.harness/tmp/\n")

    def test_al02_without_a_test_command_there_is_no_test_task(self):
        """AL-02: the test task exists only with a detected test command."""
        self.assertNotIn("Lab: tests unitaires", [t["label"] for t in lab.lab_tasks()])

    def test_al04_generated_ci_scripts_are_never_overwritten(self):
        """AL-04: a different existing script is customized-kept and untouched."""
        write(self.tmp / "ci" / "lab.sh", "mine\n")
        states = lab.install_ci(self.tmp, [])
        self.assertEqual(states["ci/lab.sh"], fusion.KEPT)
        self.assertEqual(states["ci/lab.ps1"], fusion.CREATED)
        self.assertEqual(read(self.tmp / "ci" / "lab.sh"), "mine\n")
        self.assertEqual(set(lab.install_ci(self.tmp, []).values()), {fusion.KEPT, fusion.IDENTICAL})

    def test_al05_al06_models_json_replaces_lab_models_json(self):
        """AL-05, AL-06: lab-models.json migrates to models.json (lab.* keys) and stays intact; presets merge."""
        legacy = write(self.tmp / ".claude" / "lab-models.json", '{"math": "fable", "validator": "haiku"}\n')
        before = legacy.read_bytes()
        resolved = models.resolve_root(self.tmp, models.LAB_ROLES)
        self.assertEqual(resolved["roles"]["lab.math"]["model"], "fable")
        self.assertEqual(json.loads(read(self.tmp / ".claude" / "models.json"))["lab.math"], "fable")
        self.assertEqual(legacy.read_bytes(), before)
        models.write_preset(self.tmp, "lab-experts")
        data = json.loads(read(self.tmp / ".claude" / "models.json"))
        self.assertEqual(data["lab.validator"], "haiku")

    def test_al08_core_copied_under_claude_harness(self):
        """AL-08: the core is copied under .claude/harness/ (deviation E1)."""
        got = fusion.copy_core(helpers.CORE, self.tmp)
        self.assertTrue(got["copied"])
        self.assertTrue((self.tmp / ".claude" / "harness" / "harness.py").is_file())

    def test_al09_code_index_gate_skips_headless(self):
        """AL-09: gate ai-lab.code-index, headless fallback skip."""
        self.assertEqual(gates.get("ai-lab.code-index").headless, "skip")


def usable_bash():
    """A bash that runs a script: on Windows, the PATH one may be the WSL stub, so Git Bash is tried too."""
    candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            try:
                if subprocess.run([candidate, "-c", "exit 0"], capture_output=True, timeout=20).returncode == 0:
                    return candidate
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


BASH = usable_bash()


@unittest.skipUnless(BASH, "no usable bash")
class CiScriptTest(helpers.TempDirCase):
    def run_script(self, args, protocol_text=None):
        lab.install_ci(self.tmp, ["Bash(python *)"])
        bindir = self.tmp / "bin"
        log = self.tmp / "claude-args.txt"
        write(bindir / "claude", '#!/bin/sh\nprintf "%s\\n" "$@" > "' + log.as_posix() + '"\nexit 7\n')
        os.chmod(str(bindir / "claude"), 0o755)
        if protocol_text is not None:
            write(self.tmp / "experiments" / "EXP-1.md", protocol_text)
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
        proc = subprocess.run([BASH, (self.tmp / "ci" / "lab.sh").as_posix()] + args, cwd=str(self.tmp), env=env,
                              capture_output=True, text=True, timeout=60)
        return proc, log

    def test_al07_script_syntax_is_valid(self):
        """AL-07: ci/lab.sh passes bash -n."""
        lab.install_ci(self.tmp, [])
        self.assertEqual(subprocess.run([BASH, "-n", (self.tmp / "ci" / "lab.sh").as_posix()]).returncode, 0)

    def test_al07_argument_is_mandatory(self):
        """AL-07: no protocol -> exit 2, claude never called."""
        proc, log = self.run_script([])
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(log.exists())

    def test_al07_not_pre_registered_stops_before_claude(self):
        """AL-07: a protocol whose status line is not pre-registered -> exit 1, claude never called."""
        proc, log = self.run_script(["experiments/EXP-1.md"], "Statut : PROPOSE\n")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(log.exists())

    def test_al07_call_max_turns_allow_list_and_exit_code(self):
        """AL-07: --max-turns 150, allow list with the interpreter, lab-logs/, exit code propagated."""
        proc, log = self.run_script(["experiments/EXP-1.md"], "Statut : PRE-ENREGISTRE\n")
        self.assertEqual(proc.returncode, 7)
        args = read(log).split("\n")
        self.assertIn("--max-turns", args)
        self.assertEqual(args[args.index("--max-turns") + 1], "150")
        self.assertIn("/lab-run 'experiments/EXP-1.md'", args)
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read,Glob,Grep,Write,Edit,Bash(git *),Bash(python *)")
        self.assertTrue(list((self.tmp / "lab-logs").glob("lab-*.json")))

    def test_al07_yolo_skips_permissions(self):
        """AL-07: --yolo replaces the allow list."""
        proc, log = self.run_script(["experiments/EXP-1.md", "--yolo"], "Statut : PRE-ENREGISTRE\n")
        self.assertIn("--dangerously-skip-permissions", read(log))
        self.assertNotIn("--allowedTools", read(log))

    def test_al07_powershell_twin_carries_the_same_rules(self):
        """AL-07: the PowerShell script has the same status check, turns limit and allow list."""
        text = lab.ci_scripts(["Bash(python *)"])["ci/lab.ps1"]
        for needle in ("Statut : PRE-ENREGISTRE", "'150'", "Bash(python *)", "lab-logs", "exit $status",
                       "$ErrorActionPreference = 'Stop'"):
            self.assertIn(needle, text)


def verifier(verdict="APPROVED", tests=True, protocol=True, rerun=True, issues=None):
    return "Report\n" + json.dumps({"verdict": verdict, "tests_passed": tests, "protocol_followed": protocol,
                                    "rerun_consistent": rerun, "issues": issues or []})


class EntryTest(helpers.TempDirCase):
    def test_lr02_pre_registered_protocol_goes_to_build(self):
        """LR-02: a path under experiments/ whose status line is PRE-ENREGISTRE -> Build; otherwise Cadrage."""
        write(self.tmp / "experiments" / "EXP-a.md", "# t\nStatut : PRE-ENREGISTRE\n")
        write(self.tmp / "experiments" / "EXP-b.md", "Statut : PROPOSE\n")
        write(self.tmp / "docs" / "EXP-c.md", "Statut : PRE-ENREGISTRE\n")
        self.assertEqual(labrun.entry("experiments/EXP-a.md", self.tmp),
                         {"mode": "build", "protocol": "experiments/EXP-a.md"})
        self.assertEqual(labrun.entry("'experiments/EXP-a.md'", self.tmp)["mode"], "build")
        self.assertEqual(labrun.entry("experiments/EXP-b.md", self.tmp)["mode"], "cadrage")
        self.assertEqual(labrun.entry("docs/EXP-c.md", self.tmp)["mode"], "cadrage")
        self.assertEqual(labrun.entry("suivant", self.tmp)["mode"], "cadrage")
        self.assertEqual(labrun.entry("", self.tmp)["mode"], "cadrage")

    def test_lr01_models_for_the_cycle_only_inherit_passes_no_parameter(self):
        """LR-01: --models taken out of $ARGUMENTS, aliases accepted, file untouched, inherit -> pass_param false."""
        write(self.tmp / "experiments" / "EXP-a.md", "Statut : PRE-ENREGISTRE\n")
        args = self.tmp / ".harness" / "runs" / "args.txt"
        write(args, 'experiments/EXP-a.md --models "math=fable;director.cadrage=opus"')
        proc = helpers.harness(["lab-run", "start", "--arguments-file", args.as_posix()], self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["mode"], "build")
        self.assertEqual(out["models"]["lab.math"], {"model": "fable", "pass_param": True})
        self.assertEqual(out["models"]["lab.director.cadrage"]["model"], "opus")
        self.assertEqual(out["models"]["lab.builder"], {"model": "inherit", "pass_param": False})
        self.assertFalse((self.tmp / ".claude" / "models.json").exists())
        self.assertFalse(args.exists())
        bad = helpers.harness(["lab-run", "start", "--arguments-file", write_args(self.tmp, "x --models math=gpt")], self.tmp)
        self.assertEqual(bad.returncode, 2)


def write_args(root: Path, text: str) -> str:
    return helpers.write_output(root, "bad-args.txt", text)


class MachineTest(unittest.TestCase):
    def new(self, mode="build"):
        return labrun.new_state({}, mode)

    def rec(self, state, agent, output=None, dead=False, mode=None):
        return labrun.record(state, agent, mode, output, dead)[0]

    def test_lr02_interactive_cycle_frames_then_builds_verifies_and_interprets(self):
        """LR-02: /lab-run "question" goes cadrage -> Builder -> Verifier -> interpretation."""
        state = self.new("cadrage")
        with self.assertRaises(ValueError):
            self.rec(state, labrun.BUILDER, "ok")
        self.assertEqual(self.rec(state, labrun.DIRECTOR, "framing", mode="cadrage")["action"], "continue")
        self.assertEqual(self.rec(state, labrun.BUILDER, "ok")["action"], "verify")
        self.assertEqual(self.rec(state, labrun.VERIFIER, verifier())["action"], "success")
        self.assertEqual(self.rec(state, labrun.DIRECTOR, "reading", mode="interpretation")["action"], "done")

    def test_lr05_success_requires_all_three_flags(self):
        """LR-05: APPROVED with protocol_followed=false -> fix; all true -> success."""
        state = self.new()
        self.rec(state, labrun.BUILDER, "ok")
        decision = self.rec(state, labrun.VERIFIER, verifier(protocol=False))
        self.assertEqual(decision["action"], "fix")
        self.assertEqual(decision["iteration"], 2)
        self.rec(state, labrun.BUILDER, "ok")
        for flags in ({"tests": False}, {"rerun": False}, {"verdict": "CHANGES_REQUESTED"}):
            probe = dict(state, failures={})
            decision = labrun.record(probe, labrun.VERIFIER, None, verifier(**flags), False)[0]
            self.assertEqual(decision["action"], "fix", flags)
            self.assertEqual(probe["phase"], "build")
        decision = self.rec(state, labrun.VERIFIER, verifier())
        self.assertEqual(decision["action"], "success")
        self.assertEqual(state["phase"], "interpret")

    def test_lr05_three_iterations_then_failure_with_invalidated_entry(self):
        """LR-05: 3 non-approved iterations -> fail, 'experiment invalidated' entry, no approval by exhaustion."""
        state = self.new()
        for expected in ("fix", "fix", "fail"):
            self.rec(state, labrun.BUILDER, "ok")
            decision = self.rec(state, labrun.VERIFIER, verifier("CHANGES_REQUESTED", issues=[
                {"file": "a.py", "severity": "major", "kind": "protocol", "description": "d"}]))
            self.assertEqual(decision["action"], expected)
        self.assertEqual(decision["journal_entry"], "experiment invalidated")
        self.assertEqual(state["status"], "failed")
        with self.assertRaises(labrun.RunOver):
            self.rec(state, labrun.BUILDER, "ok")

    def test_lr06_dead_verifier_relaunched_without_consuming_an_iteration(self):
        """LR-06: Verifier dead then alive -> iteration not consumed; dead twice -> fail."""
        state = self.new()
        self.rec(state, labrun.BUILDER, "ok")
        self.assertEqual(self.rec(state, labrun.VERIFIER, dead=True)["action"], "relaunch")
        self.assertEqual(state["iteration"], 1)
        decision = self.rec(state, labrun.VERIFIER, verifier())
        self.assertEqual((decision["action"], decision["iteration"]), ("success", 1))
        state = self.new()
        self.rec(state, labrun.BUILDER, "ok")
        self.rec(state, labrun.VERIFIER, "no json at all")
        self.assertEqual(self.rec(state, labrun.VERIFIER, dead=True)["action"], "fail")

    def test_lr06_dead_builder_relaunched_once_then_failure(self):
        """LR-06: Builder dead -> one relaunch, then explicit failure."""
        state = self.new()
        self.assertEqual(self.rec(state, labrun.BUILDER, dead=True)["action"], "relaunch")
        self.assertEqual(self.rec(state, labrun.BUILDER, dead=True)["action"], "fail")
        self.assertEqual(state["status"], "failed")

    def test_lr03_dead_expert_twice_is_named_not_collected(self):
        """LR-03: an expert dead twice -> 'expertise <neuro|math> not collected', the cycle goes on; alive again clears the count."""
        state = self.new("cadrage")
        self.assertEqual(self.rec(state, labrun.NEURO, dead=True)["action"], "relaunch")
        decision = self.rec(state, labrun.NEURO, dead=True)
        self.assertEqual(decision["action"], "continue")
        self.assertIn("expertise neuro not collected", decision["note"])
        self.assertEqual(decision["uncollected"], ["neuro"])
        self.assertEqual(self.rec(state, labrun.MATH, "avis")["action"], "continue")
        self.assertEqual(state["uncollected"], ["neuro"])

    def test_lr03_interpretation_experts_are_covered_too(self):
        """LR-03 (C2): a dead expert in the interpretation phase is relaunched, then named."""
        state = self.new()
        self.rec(state, labrun.BUILDER, "ok")
        self.rec(state, labrun.VERIFIER, verifier())
        self.assertEqual(self.rec(state, labrun.MATH, dead=True)["action"], "relaunch")
        self.assertIn("expertise math not collected", self.rec(state, labrun.MATH, dead=True)["note"])

    def test_lr04_director_dead_twice_fails(self):
        """LR-04: the Director is not optional: dead twice -> failure, no pre-registration gate."""
        state = self.new("cadrage")
        self.rec(state, labrun.DIRECTOR, dead=True, mode="cadrage")
        self.assertEqual(self.rec(state, labrun.DIRECTOR, dead=True, mode="cadrage")["action"], "fail")
        for gate in ("lab-run.pi-questions", "lab-run.preregistration", "lab-run.consignation"):
            self.assertEqual(gates.get(gate).headless, "stop" if gate != "lab-run.consignation" else "default")

    def test_lr07_cross_interpretation_once_after_approval(self):
        """LR-07: interpretation refused before approval, runs once, refused a second time."""
        state = self.new()
        with self.assertRaises(ValueError):
            self.rec(state, labrun.DIRECTOR, "x", mode="interpretation")
        self.rec(state, labrun.BUILDER, "ok")
        self.rec(state, labrun.VERIFIER, verifier())
        self.assertEqual(self.rec(state, labrun.DIRECTOR, "verdict", mode="interpretation")["action"], "done")
        self.assertEqual(state["status"], "success")
        with self.assertRaises(labrun.RunOver):
            self.rec(state, labrun.DIRECTOR, "again", mode="interpretation")

    def test_lr04_08_status_values_and_gates(self):
        """LR-04, LR-08: the three lab-run gates exist; consignation falls back to its default headless."""
        self.assertEqual(gates.get("lab-run.consignation").default, "approval")
        self.assertEqual(gates.get("lab-run.preregistration").kind, "a")

    def test_lr09_lab_verifier_schema(self):
        """LR-09: kind in protocol|leak|stats|code|convention; both flags are booleans."""
        good = verdicts.parse_lab_verifier(verifier(issues=[
            {"file": "a", "severity": "minor", "kind": "stats", "description": "d"}]))
        self.assertEqual(good["issues"][0]["kind"], "stats")
        for text in (verifier(issues=[{"file": "a", "severity": "minor", "kind": "style", "description": "d"}]),
                     json.dumps({"verdict": "APPROVED", "tests_passed": True, "issues": []}),
                     "no block"):
            with self.assertRaises(verdicts.UnusableOutput):
                verdicts.parse_lab_verifier(text)

    def test_lr_roles_map_to_model_roles(self):
        """LR-01: every agent call maps to a role of the closed set."""
        for agent, mode in ((labrun.DIRECTOR, "cadrage"), (labrun.DIRECTOR, "interpretation"), (labrun.NEURO, None),
                            (labrun.MATH, None), (labrun.BUILDER, None), (labrun.VERIFIER, None)):
            self.assertIn(labrun.role_of(agent, mode), models.LAB_ROLES)


class JournalTest(helpers.TempDirCase):
    def test_lr05_cli_cycle_journal_is_valid(self):
        """LR-05, LR-06, CA-26: a scripted cycle through the CLI; the journal validates and is named lab-run-*."""
        write(self.tmp / "experiments" / "EXP-a.md", "Statut : PRE-ENREGISTRE\n")
        args = helpers.write_output(self.tmp, "a.txt", "experiments/EXP-a.md")
        start = json.loads(helpers.harness(["lab-run", "start", "--arguments-file", args], self.tmp).stdout)
        run = start["run"]

        def record(agent, text=None, dead=False, mode=None):
            cmd = ["lab-run", "record", "--run", run, "--agent", agent, "--duration-ms", "5", "--tokens", "9"]
            if mode:
                cmd += ["--mode", mode]
            if dead:
                cmd.append("--dead")
            else:
                cmd += ["--output", helpers.write_output(self.tmp, "o.txt", text or "ok")]
            proc = helpers.harness(cmd, self.tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)

        self.assertEqual(record("lab-builder")["action"], "verify")
        self.assertEqual(record("lab-verifier", dead=True)["action"], "relaunch")
        self.assertEqual(record("lab-verifier", verifier(protocol=False))["action"], "fix")
        record("lab-builder")
        self.assertEqual(record("lab-verifier", verifier())["action"], "success")
        self.assertEqual(record("lab-neuro", dead=True)["action"], "relaunch")
        self.assertIn("expertise neuro not collected", record("lab-neuro", dead=True)["note"])
        self.assertEqual(record("lab-director", "verdict", mode="interpretation")["action"], "done")
        validate = helpers.harness(["journal", "validate", run], self.tmp)
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertTrue(json.loads(validate.stdout)["name_conforms"])
        self.assertRegex(Path(run).name, journal.NAME_RE)


if __name__ == "__main__":
    unittest.main()
