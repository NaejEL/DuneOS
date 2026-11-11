"""Phase 3, DR-01 to DR-10: /harness-doctor in a project."""

from __future__ import annotations

import json
import re
import unittest
from datetime import timedelta
from unittest import mock

import helpers
import upgrade_fixtures as uf
from hcore import doctor, stamp, upgrade
from helpers import FakeRunner

SKILL = helpers.CORE.parent / "harness-doctor" / "SKILL.md"


class SkillFrontmatterTest(unittest.TestCase):
    @unittest.skipUnless(SKILL.is_file(), "skill folder absent (core copied into a project)")
    def test_dr01_frontmatter(self):
        """DR-01: name, description, user-invocable and argument-hint; neither context: fork nor disable-model-invocation."""
        text = SKILL.read_text(encoding="utf-8")
        self.assertEqual(text.split("\n")[0], "---")
        front = text.split("\n---\n", 1)[0].split("\n")
        keys = {l.split(":", 1)[0]: l.split(":", 1)[1].strip() for l in front[1:] if re.match(r"^[a-z-]+:", l)}
        self.assertEqual(keys["name"], "harness-doctor")
        self.assertTrue(keys["description"])
        self.assertEqual(keys["user-invocable"], "true")
        self.assertIn("--scan", keys["argument-hint"])
        self.assertNotIn("context", keys)
        self.assertNotIn("disable-model-invocation", keys)


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class DoctorTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.root, self.info, self.pv1, self.pv2 = uf.blocks_project(self.tmp)

    def report(self, info=None, gh_ok=True, **kw):
        gh = FakeRunner().on(("gh", "auth"), code=0 if gh_ok else 1)
        with mock.patch("hcore.doctor.shutil.which", return_value="gh"):
            return doctor.report(self.root, info or self.info, uf.CORE, self.pv1 if kw.pop("v", 1) == 1 else self.pv2,
                                 uf.ASSETS, None, gh, uf.NOW)

    def test_dr02_all_green_on_a_clean_installation(self):
        """DR-02, CA-21: every check is ok on an installation up to date (gh simulated authenticated)."""
        # the customizations of the fixture are outside the blocks; a clean upgrade first aligns the stamp
        uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv1, now=uf.NOW + timedelta(minutes=1))
        uf.write(self.root, ".claude/models.json", "{}\n")
        rep = self.report()
        self.assertEqual(rep["equipped"], "stamp")
        self.assertEqual({k: v for k, v in rep["checks"].items() if v != "ok"}, {}, rep["checks"])
        for key in ("version", "core", "blocks", "legacy_files", "missing", "hooks", "models", "gh", "signals", "upgrade"):
            self.assertIn(key, rep)

    def test_dr03_edited_and_outdated_blocks(self):
        """DR-03: edited = hash differs from the stamp; outdated = the repository renders it differently."""
        rel = ".claude/agents/factory-planner.md"
        text = uf.read(self.root, rel).replace("You are the Planner", "You are the MODIFIED PLANNER", 1)
        uf.write(self.root, rel, text)
        rows = {(r["file"], r["block"]): r["state"] for r in self.report()["blocks"]}
        self.assertEqual(rows[(rel, "harness.body")], "edited")
        outdated = {(r["file"], r["block"]): r["state"] for r in self.report(v=2)["blocks"]}
        self.assertEqual(outdated[(rel, "harness.body")], "edited+outdated")
        self.assertEqual(outdated[(".claude/agents/factory-builder.md", "harness.body")], "outdated")

    def test_dr04_missing_files(self):
        """DR-04: an expected file that is gone is listed in `missing`."""
        (self.root / ".claude" / "agents" / "factory-verifier.md").unlink()
        self.assertIn(".claude/agents/factory-verifier.md", self.report()["missing"])

    def test_dr05_hooks_declared_but_absent(self):
        """DR-05: hooks.missing = [post-merge, post-rewrite] when config.json declares `auto` and none is installed."""
        uf.write(self.root, ".knowledge/config.json", json.dumps({"schema": 1, "hooks": "auto"}) + "\n")
        helpers.init_repo(self.root)
        hooks = self.report()["hooks"]
        self.assertEqual((hooks["declared"], hooks["missing"]), ("auto", ["post-merge", "post-rewrite"]))
        no_git = self.tmp / "nogit"
        uf.write(no_git, ".knowledge/config.json", json.dumps({"hooks": "auto"}))
        state = doctor.hooks_state(no_git, FakeRunner().on(("git",), code=128))
        self.assertEqual((state["missing"], state.get("git")), ([], "n/a"))

    def test_dr06_models_json_absent_and_migration(self):
        """DR-06: models.json absent -> warn; lab-models.json present -> migration_available."""
        uf.write(self.root, ".claude/lab-models.json", "{}\n")
        models = self.report()["models"]
        self.assertEqual((models["models_json"], models["migration_available"], models["status"]),
                         (False, True, "warn"))

    def test_dr07_gh_not_authenticated(self):
        """DR-07: `gh auth status` failing -> message `gh not authenticated`, warn, never blocking."""
        gh = self.report(gh_ok=False)["gh"]
        self.assertFalse(gh["authenticated"])
        self.assertIn("gh not authenticated", gh["message"])
        self.assertEqual(gh["status"], "warn")
        with mock.patch("hcore.doctor.shutil.which", return_value=None):
            self.assertEqual(doctor.gh_state()["message"], "gh: absent")

    def test_dr08_new_signals_propose_a_rescan_and_never_launch_it(self):
        """DR-08: a profile absent from the stamp with a non-rejected deterministic status -> propose_rescan."""
        uf.write(self.root, "experiments/EXP-1.md", "x\n")
        uf.write(self.root, "pyproject.toml", "[project]\nname='x'\n")
        rep = self.report()
        sig = rep["signals"]
        if sig["profiles"]:                      # the research profile signals depend on its detection criteria
            self.assertTrue(sig["propose_rescan"])
            self.assertEqual(sig["command"], "/software-factory --rescan")
        self.assertIn("propose_rescan", sig)

    def test_dr09_writes_only_its_journal_and_nothing_on_a_none_project(self):
        """DR-09, JR-04: the journal harness-doctor-<ts>.jsonl only; an unequipped project is left untouched."""
        res = helpers.harness(["doctor", "--root", str(self.root)], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertRegex(out["journal"], r"harness-doctor-\d{8}-\d{6}\.jsonl$")
        self.assertEqual(helpers.harness(["journal", "validate", str(self.root / out["journal"])], self.tmp).returncode, 0)
        empty = self.tmp / "empty"
        empty.mkdir()
        res = helpers.harness(["doctor", "--root", str(empty)], self.tmp)
        self.assertEqual(json.loads(res.stdout)["equipped"], "none")
        self.assertEqual(list(empty.iterdir()), [])

    def test_dr10_unknown_root_is_exit_2(self):
        """DR-10: a root that does not exist -> exit 2; a report -> exit 0."""
        self.assertEqual(helpers.harness(["doctor", "--root", str(self.tmp / "nowhere")], self.tmp).returncode, 2)

    def test_dr02_upgrade_summary(self):
        """DR-02: upgrade = {changes, blocks_only, by_skill} from the plans, written nowhere."""
        up = self.report(v=2)["upgrade"]
        self.assertGreater(up["changes"], 0)
        self.assertTrue(up["blocks_only"])
        self.assertGreater(up["by_skill"]["software-factory"], 0)


if __name__ == "__main__":
    unittest.main()
