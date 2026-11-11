"""Phase 7, PL-01 to PL-03 and VS-05: plugin manifest, effective skill name, version guard, version source."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hcore import plugin, version  # noqa: E402


def write_manifest(root, **fields):
    path = Path(root) / ".claude-plugin" / "plugin.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict({"name": "harness", "version": "1.2.3"}, **fields)), encoding="utf-8")


class EffectiveName(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hcore-plugin-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_pl02_plugin_mode_prefixes_with_the_plugin_name(self):
        write_manifest(self.tmp)
        out = plugin.effective_name("ship-pr", str(self.tmp / "skills" / "audit"), {})
        self.assertEqual((out["name"], out["mode"]), ("harness:ship-pr", "plugin"))

    def test_pl02_links_mode_keeps_the_short_name(self):
        out = plugin.effective_name("ship-pr", str(self.tmp / ".claude" / "skills" / "audit"), {})
        self.assertEqual((out["name"], out["mode"]), ("ship-pr", "links"))

    def test_pl02_a_link_does_not_become_plugin_mode_through_its_target(self):
        # The folder is not resolved: ~/.claude/skills/x has no manifest around it even if x points to a plugin.
        write_manifest(self.tmp)
        out = plugin.effective_name("ship-pr", str(self.tmp / "home" / ".claude" / "skills" / "audit"), {})
        self.assertEqual(out["mode"], "links")

    def test_pl02_without_skill_dir_the_plugin_root_variable_decides(self):
        write_manifest(self.tmp)
        self.assertEqual(plugin.effective_name("x", None, {"CLAUDE_PLUGIN_ROOT": str(self.tmp)})["name"], "harness:x")
        self.assertEqual(plugin.effective_name("x", None, {})["name"], "x")

    def test_pl01_unreadable_manifest_is_absent(self):
        path = self.tmp / ".claude-plugin" / "plugin.json"
        path.parent.mkdir()
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(plugin.read_manifest(self.tmp))
        self.assertEqual(plugin.effective_name("x", str(self.tmp / "skills" / "y"), {})["name"], "x")


class VersionSource(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hcore-plugin-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_vs05_manifest_version_without_git(self):
        write_manifest(self.tmp)
        info = version.repo_info(self.tmp, run=lambda args: type("R", (), {"ok": False, "out": ""})())
        self.assertEqual((info["describe"], info["commit"]), ("1.2.3", None))

    def test_vs05_no_manifest_falls_back_on_git_or_core_version(self):
        info = version.repo_info(self.tmp, run=lambda args: type("R", (), {"ok": False, "out": ""})())
        self.assertTrue(info["describe"].startswith("core-"))

    def test_vs05_manifest_version_wins_over_git_describe(self):
        write_manifest(self.tmp)

        def fake(args):
            out = {"rev-parse": "abc123\n", "describe": "v9-3-gabc123-dirty\n"}[args[1]]
            return type("R", (), {"ok": True, "out": out})()
        info = version.repo_info(self.tmp, run=fake)
        self.assertEqual((info["describe"], info["commit"], info["dirty"]), ("1.2.3", "abc123", True))


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hcore-plugin-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def git(self, *args):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
                       + list(args), cwd=str(self.tmp), check=True, capture_output=True, env=env)

    def test_pl03_change_without_bump_fails_and_bump_passes(self):
        write_manifest(self.tmp)
        (self.tmp / "skills" / "a").mkdir(parents=True)
        (self.tmp / "skills" / "a" / "SKILL.md").write_text("one\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.assertTrue(plugin.version_guard(self.tmp)["ok"])
        (self.tmp / "skills" / "a" / "SKILL.md").write_text("two\n", encoding="utf-8")
        stale = plugin.version_guard(self.tmp)
        self.assertEqual((stale["ok"], stale["files"]), (False, ["skills/a/SKILL.md"]))
        write_manifest(self.tmp, version="1.2.4")
        self.assertEqual(plugin.version_guard(self.tmp)["status"], "bumped")

    def test_pl03_no_manifest_or_no_git_is_not_a_failure(self):
        self.assertEqual(plugin.version_guard(self.tmp)["status"], "no_plugin")
        write_manifest(self.tmp)
        self.assertTrue(plugin.version_guard(self.tmp)["ok"])      # temp folder: not a git repository (or no baseline)


if __name__ == "__main__":
    unittest.main()
