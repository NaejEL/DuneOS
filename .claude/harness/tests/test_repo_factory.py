# Factory of the claude-skills repository itself, regenerated from the
# software-factory template through the 'sf' commands (UF-01 to UF-07, CA-31).
# Skipped when the core is copied into another project.

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import helpers
from hcore import factory, fusion, gates, models

REPO = helpers.CORE.parent.parent
SKILLS_DIR = REPO / "skills"
AGENTS = REPO / ".claude" / "agents"
FACTORY_RUN = REPO / ".claude" / "skills" / "factory-run" / "SKILL.md"
HARNESS = REPO / ".claude" / "harness"
IN_REPO = (SKILLS_DIR / "_core" / "harness.py").is_file() and FACTORY_RUN.is_file()
ROLES = {"factory-planner": "factory.planner", "factory-builder": "factory.builder",
         "factory-verifier": "factory.verifier"}
# Since the core 4.0 conversion (c3c3f3d) the generated files carry the interpreter
# retained at generation time (`py -3` here) and the repository house rules sit
# outside the regenerable blocks.
UNITTEST = "cd skills/_core/tests && py -3 -B -m unittest discover -s . -t ."
WHITELIST = "Read,Glob,Grep,Write,Edit,Bash(git *),Bash(py *)"
CORE_COMMANDS = ("py -3 .claude/harness/harness.py factory-run start", "py -3 .claude/harness/harness.py gate decide")


def allowed(command: str, script_text: str) -> bool:
    """True when a Bash(<pattern>) entry of the script's --allowedTools allows command."""
    value = re.search(r"--allowedTools['\"]?,? ?['\"]([^'\"]+)['\"]", script_text).group(1)
    patterns = re.findall(r"Bash\(([^)]*)\)", value)
    return any(fnmatch.fnmatchcase(command, pattern) for pattern in patterns)


STRICT = os.environ.get("HARNESS_CHECK_COPY") == "1"


def copy_is_consistent(dest: Path) -> list:
    """Internal consistency of a core copy, whatever the core it is compared
    with: recognized, runnable ('version' answers its own CORE_VERSION), no
    bytecode written. Returns the problems found."""
    problems = []
    if not fusion.is_core_copy(dest):
        return ["%s is not a recognized core copy" % dest]
    declared = re.search(r'^CORE_VERSION = "([^"]+)"$', text(dest / "hcore" / "__init__.py"), re.M).group(1)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    res = subprocess.run([sys.executable, "-B", str(dest / "harness.py"), "version"], capture_output=True,
                         text=True, encoding="utf-8", env=env, timeout=60)
    if res.returncode != 0:
        problems.append("harness.py version: exit %d: %s" % (res.returncode, res.stderr.strip()))
    elif json.loads(res.stdout).get("core_version") != declared:
        problems.append("harness.py version does not answer its own CORE_VERSION %s" % declared)
    if any(dest.rglob("__pycache__")):
        problems.append("bytecode left in the copy")
    return problems


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(path: Path) -> str:
    match = re.match(r"\A---\r?\n(.*?)^---\r?$", text(path), re.S | re.M)
    assert match, path
    return match.group(1)


@unittest.skipUnless(IN_REPO, "not the claude-skills repository (core copied into a project)")
class RepoFactoryTest(unittest.TestCase):
    def test_uf01_generated_from_the_template_with_model_blocks(self):
        """UF-01: the three agents carry the model of their role from .claude/models.json
        (absent -> inherit) inside the regenerable harness.frontmatter block, with the
        template fields; factory-run has the template frontmatter."""
        expected = models.resolve_root(REPO, models.FACTORY_ROLES)["roles"]
        for name, role in ROLES.items():
            path = AGENTS / ("%s.md" % name)
            body = text(path)
            fm = frontmatter(path)
            self.assertIn("# >>> harness.frontmatter >>>", body, name)
            self.assertIn("name: %s\n" % name, fm)
            if expected[role]["model"] == "inherit":
                self.assertNotRegex(fm, r"(?m)^model:", name)
            else:
                self.assertIn("model: %s\n" % expected[role]["model"], fm, name)
        self.assertIn("tools: Read, Glob, Grep\n", frontmatter(AGENTS / "factory-planner.md"))
        self.assertIn("tools: Read, Glob, Grep, Bash\n", frontmatter(AGENTS / "factory-verifier.md"))
        self.assertNotIn("tools:", frontmatter(AGENTS / "factory-builder.md"))
        fm = frontmatter(FACTORY_RUN)
        self.assertIn("name: factory-run\n", fm + "\n")
        self.assertIn("disable-model-invocation: true", fm)
        self.assertRegex(fm, r'(?m)^argument-hint: ".*--models ')

    def test_uf02_ca31_planner_describes_the_layout_and_core_is_not_a_skill(self):
        """UF-02, CA-31: the Planner house rules describe the skills layout and the core
        under skills/_core/ (the per-skill list was dropped in the 4.0 conversion, so the
        skill inventory is no longer duplicated there); _core is not a skill (C1)."""
        planner = text(AGENTS / "factory-planner.md")
        names = sorted(p.name for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())
        self.assertEqual(len(names), 12)
        self.assertNotIn("_core", names)
        self.assertIn("`skills/<name>/SKILL.md`", planner)
        self.assertIn("`skills/_core/`", planner)
        self.assertFalse((SKILLS_DIR / "_core" / "SKILL.md").exists())

    def test_uf03_ca31_builder_and_verifier_gates(self):
        """UF-03, CA-31: the house-rule gates of both roles: the unittest command of the
        core with the retained interpreter, bash -n, ci/test-harness, no __pycache__."""
        for name in ("factory-builder", "factory-verifier"):
            body = text(AGENTS / ("%s.md" % name))
            self.assertIn("bash -n install_skills.sh", body, name)
            self.assertIn("`%s`" % UNITTEST, body, name)
            self.assertIn("bash ci/test-harness.sh", body, name)
            self.assertIn("__pycache__", body, name)
            self.assertNotIn("command -v timeout", body, name)
        self.assertRegex(text(AGENTS / "factory-builder.md"), "`name` equal to the folder name")
        self.assertRegex(text(AGENTS / "factory-verifier.md"),
                         "valid frontmatter of the touched `SKILL.md`")

    def test_uf04_ca11_factory_run_rebranched_on_the_core(self):
        """UF-04, CA-31: factory-run driven through the core copy, journal, gates
        factory-run.questions / approval with their options, the 'Verifier muet' rule,
        --models, and the house rule that this repository is never self-delivered.
        (The 4.0 template dropped the FR-xx labels, the need gate and the approved-line
        wording, so they are no longer asserted.)"""
        body = text(FACTORY_RUN)
        self.assertIn("`H` stands for `py -3 .claude/harness/harness.py`", body)
        self.assertNotIn("command -v timeout", body)
        for needle in (".claude/harness/harness.py", "H factory-run start --arguments-file",
                       "H factory-run record --run <run> --agent <name>", ".harness/runs/factory-run-<timestamp>.jsonl",
                       "H journal amend", "H begin --run <run> --slot <name>", "`success`", "`fix`", "`relaunch`",
                       "`fail`", "A silent Verifier is not a satisfied Verifier", "Never approve by exhaustion",
                       "`pass_param`", "--headless", "--no-ship"):
            self.assertIn(needle, body)
        # house rules (out of the generated blocks, owned by the repository)
        self.assertRegex(body, "never `factory-run deliver`")
        for gid in ("factory-run.questions", "factory-run.approval"):
            gate = gates.get(gid)
            self.assertIn(gid, body)
            for option in gate.options:
                self.assertIn(option, body, (gid, option))

    def test_uf05_harness_is_a_consistent_core_copy(self):
        """UF-05 (Q-C), in T: .claude/harness/ is a recognized, runnable core copy.
        A core that evolves in a lot does not make T fail: the strict identity is
        checked on demand (HARNESS_CHECK_COPY=1, or 'sf copy-core --check')."""
        self.assertTrue(HARNESS.is_dir(), "copy missing: run 'sf copy-core --root .'")
        self.assertEqual(copy_is_consistent(HARNESS), [])

    @unittest.skipUnless(STRICT, "strict copy check on demand: HARNESS_CHECK_COPY=1")
    def test_uf05_ca31_strict_identity_on_demand(self):
        """UF-05, CA-31 (on demand): .claude/harness/ is identical to skills/_core/
        (no file missing, extra or different); 'sf copy-core --check' exits 0."""
        self.assertEqual(fusion.check_core_copy(SKILLS_DIR / "_core", REPO)["identical"], True)
        res = helpers.harness(["sf", "copy-core", "--root", str(REPO), "--check"], REPO)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_uf06_ca31_ci_whitelist_and_syntax(self):
        """UF-06, CA-31: --allowedTools = the template base plus git and the core
        interpreter retained at generation time (Bash(py *)), in both scripts; bash -n
        and the PowerShell parser accept them."""
        sh, ps1 = REPO / "ci" / "factory.sh", REPO / "ci" / "factory.ps1"
        self.assertIn('--allowedTools "%s"' % WHITELIST, text(sh))
        self.assertIn("'--allowedTools', '%s'" % WHITELIST, text(ps1))
        self.assertIn("Bash(py *)", text(sh))
        # The core calls run in headless mode.
        for script in (sh, ps1):
            for command in CORE_COMMANDS:
                self.assertTrue(allowed(command, text(script)), (script.name, command))
            self.assertFalse(allowed('t=; command -v timeout >/dev/null && t="timeout 5"', text(script)))
        for script in (sh, ps1):
            body = text(script)
            self.assertIn("Statut : APPROUVEE", body)
            self.assertIn("--max-turns", body)
            self.assertIn("factory-logs", body)
        self.assertIn("set -euo pipefail", text(sh))
        self.assertIn("$ErrorActionPreference = 'Stop'", text(ps1))
        bash = shutil.which("bash")
        if bash:
            res = subprocess.run([bash, "-n", str(sh)], capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
        pwsh = shutil.which("pwsh")
        if pwsh:
            command = "[scriptblock]::Create((Get-Content -LiteralPath '%s' -Raw)) | Out-Null" % ps1
            res = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True,
                                 timeout=120)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_uf07_ca31_repository_rules_kept(self):
        """UF-07, CA-31: never run install_skills.* (Builder and Verifier house rules); the
        Verifier judges the .sh / .ps1 pair by reading. The 4.0 house rules replaced the
        accent and equivalence wording with the English-code rule."""
        for name in ("factory-builder", "factory-verifier"):
            body = text(AGENTS / ("%s.md" % name))
            self.assertRegex(body, r"\*\*Never\*\* run `install_skills\.sh` or `install_skills\.ps1`", name)
        self.assertRegex(text(AGENTS / "factory-verifier.md"), "consistency between the two")
        self.assertRegex(text(AGENTS / "factory-builder.md"),
                         "Code, comments, strings and Markdown prose in English")


class CoreCopyTest(helpers.TempDirCase):
    def evolved_core(self) -> Path:
        core = self.tmp / "core"
        shutil.copytree(str(helpers.CORE), str(core), ignore=shutil.ignore_patterns("__pycache__"))
        path = core / "hcore" / "relaunch.py"
        path.write_text(path.read_text(encoding="utf-8") + "# evolved in a later lot\n", encoding="utf-8")
        return core

    def test_uf05_consistency_check_survives_a_core_evolution(self):
        """UF-05: the T-level check of the copy does not fail when the core evolves
        (the strict comparison does, on demand)."""
        root = self.tmp / "project"
        fusion.copy_core(helpers.CORE, root)
        evolved = self.evolved_core()
        strict = fusion.check_core_copy(evolved, root)
        self.assertEqual((strict["identical"], strict["differs"]), (False, ["hcore/relaunch.py"]))
        self.assertEqual(copy_is_consistent(root / ".claude" / "harness"), [])
        res = helpers.harness(["sf", "copy-core", "--root", str(root), "--check"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_uf05_refresh_replaces_a_recognized_copy_and_never_a_foreign_file(self):
        """UF-05 (sf copy-core --refresh): a recognized copy gets every core file
        (created or replaced); a file that is not a core file is never touched
        and is reported; an unrecognized .claude/harness/ is left as it is (exit 1);
        --check exits 1 while the copy differs."""
        root = self.tmp / "project"
        res = helpers.harness(["sf", "copy-core", "--root", str(root), "--refresh"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        dest = root / ".claude" / "harness"
        self.assertTrue(fusion.check_core_copy(helpers.CORE, root)["identical"])
        (dest / "hcore" / "relaunch.py").write_text("# stale\n", encoding="utf-8")
        (dest / "hcore" / "gates.py").unlink()
        (dest / "NOTES.txt").write_text("mine\n", encoding="utf-8")
        self.assertEqual(helpers.harness(["sf", "copy-core", "--root", str(root), "--check"], self.tmp).returncode, 1)
        out = json.loads(helpers.harness(["sf", "copy-core", "--root", str(root), "--refresh"], self.tmp).stdout)
        self.assertEqual(out["files"][".claude/harness/hcore/relaunch.py"], fusion.MERGED)
        self.assertEqual(out["files"][".claude/harness/hcore/gates.py"], fusion.CREATED)
        self.assertEqual(out["foreign"], [".claude/harness/NOTES.txt"])
        self.assertEqual((dest / "NOTES.txt").read_text(encoding="utf-8"), "mine\n")
        check = fusion.check_core_copy(helpers.CORE, root)
        self.assertEqual((check["differs"], check["missing"], check["extra"]), ([], [], ["NOTES.txt"]))
        other = self.tmp / "other"
        (other / ".claude" / "harness").mkdir(parents=True)
        (other / ".claude" / "harness" / "harness.py").write_text("print('mine')\n", encoding="utf-8")
        res = helpers.harness(["sf", "copy-core", "--root", str(other), "--refresh"], self.tmp)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual(sorted(p.name for p in (other / ".claude" / "harness").iterdir()), ["harness.py"])
        self.assertEqual((other / ".claude" / "harness" / "harness.py").read_text(encoding="utf-8"), "print('mine')\n")


if __name__ == "__main__":
    unittest.main()
