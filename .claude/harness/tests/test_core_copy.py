"""Phase 3, CO-01 to CO-07: history of the core, MANIFEST and update of the copy .claude/harness/."""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path

import helpers
import upgrade_fixtures as uf
from hcore import history, upgrade
from hcore.ghflow import Stop

H = helpers.harness


def core_variant(dest: Path, change: str = "") -> Path:
    """A core tree (without tests) and its history.json = [original state, changed state]."""
    shutil.copytree(str(helpers.CORE), str(dest), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    return dest


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class CoreCopyTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        repo, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo)
        self.pv1 = uf.make_profiles(self.tmp)
        # two states of the core: A (older) and B (current); both carry the same history.json
        self.core_a = core_variant(self.tmp / "core_a")
        self.core_b = core_variant(self.tmp / "core_b")
        marker = self.core_a / "hcore" / "blocks.py"
        marker.write_text(marker.read_text(encoding="utf-8") + "\n# state A of the core\n", encoding="utf-8")
        second = self.core_a / "hcore" / "counters.py"
        second.write_text(second.read_text(encoding="utf-8") + "\n# state A of the core\n", encoding="utf-8")
        entries = []
        for core in (self.core_a, self.core_b):
            table = history.table(core)
            entries.append({"id": history.entry_id(table), "commit": None, "core_version": "3.0.0", "files": table})
        for core in (self.core_a, self.core_b):
            (core / "history.json").write_text(history.dumps(entries), encoding="utf-8")
        self.root = self.tmp / "proj"

    def install(self, core: Path) -> None:
        plan = upgrade.build_plan(self.root, upgrade.SF, self.info, core, self.pv1, requested=["software"],
                                  interpreter=uf.INTERPRETER, now=uf.NOW, assets=uf.ASSETS)
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)

    def upgrade_to(self, core: Path):
        return upgrade.build_plan(self.root, upgrade.SF, self.info, core, self.pv1, interpreter=uf.INTERPRETER,
                                  now=uf.NOW, assets=uf.ASSETS)

    def project_core(self) -> Path:
        return self.root / ".claude" / "harness"

    def test_co01_check_names_the_changed_file(self):
        """CO-01: `core history --check` is 0 on a tree that is an entry of history.json, 1 and the file otherwise."""
        copy = core_variant(self.tmp / "copy")
        shutil.copy(str(self.core_b / "history.json"), str(copy / "history.json"))
        ok = H(["core", "history", "--check"], self.tmp)
        self.assertEqual(subprocess_check(copy), 0)
        target = copy / "hcore" / "counters.py"
        target.write_bytes(target.read_bytes() + b"#")
        code, out = subprocess_check(copy, True)
        self.assertEqual(code, 1)
        self.assertIn("hcore/counters.py", out)
        self.assertEqual(ok.returncode in (0, 1), True)

    def test_co01_rebuild_from_git(self):
        """CO-01: `core history --rebuild` regenerates the file from the git history (one entry per distinct state)."""
        repo, _ = uf.make_repo(self.tmp / "g", 1)
        core = repo / "skills" / "_core"
        (core / "hcore" / "counters.py").write_text(
            (core / "hcore" / "counters.py").read_text(encoding="utf-8") + "\n# second state\n", encoding="utf-8")
        helpers.git(repo, "add", "-A")
        helpers.git(repo, "commit", "-q", "-m", "second state")
        res = helpers.subprocess.run([helpers.sys.executable, "-B", str(core / "harness.py"), "core", "history",
                                      "--rebuild"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        entries = json.loads((core / "history.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(entries), 2)
        self.assertEqual(entries[-1]["id"], history.entry_id(history.table(core)))
        self.assertEqual(subprocess_check(core), 0)

    def test_co02_manifest_lists_the_hashes_of_the_copied_files(self):
        """CO-02: MANIFEST.json = {core_version, repo, entry, files} with the sha256 of every copied file."""
        self.install(self.core_a)
        manifest = json.loads((self.project_core() / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {"core_version", "repo", "entry", "files"})
        for rel, digest in manifest["files"].items():
            self.assertEqual(history.sha((self.project_core() / rel).read_bytes()), digest, rel)
        self.assertEqual(manifest["repo"]["commit"], self.info["commit"])

    def test_co03_co04_pristine_files_are_updated_without_manifest(self):
        """CO-03, CO-04: no MANIFEST: files known to history.json are pristine; the copy becomes the new core."""
        self.install(self.core_a)
        (self.project_core() / "MANIFEST.json").unlink()
        self.assertEqual(set(history.copy_states(self.core_b, self.project_core(), history.load(self.core_b)).values()),
                         {history.PRISTINE})
        plan = self.upgrade_to(self.core_b)
        self.assertIn(".claude/harness/hcore/blocks.py", [c["file"] for c in plan["changes"]])
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        a = uf.tree(self.project_core())
        b = uf.tree(self.core_b)
        a.pop("MANIFEST.json")
        self.assertEqual(a, b)

    def test_co03_extra_files_are_kept_and_reported(self):
        """CO-03: an `extra` file is never deleted, and it is reported."""
        self.install(self.core_a)
        (self.project_core() / "mine.txt").write_text("mine", encoding="utf-8")
        plan = self.upgrade_to(self.core_b)
        self.assertTrue(any("mine.txt" in n for n in plan["notes"]))
        self.assertNotIn(".claude/harness/mine.txt", [c["file"] for c in plan["changes"]])

    def test_co04_a_customized_file_blocks_everything(self):
        """CO-04: one customized file -> exit 1, `core not upgraded`, nothing written, stamp included."""
        self.install(self.core_a)
        (self.project_core() / "MANIFEST.json").unlink()
        target = self.project_core() / "hcore" / "counters.py"
        target.write_bytes(target.read_bytes() + b"#")
        before = uf.tree(self.root)
        with self.assertRaises(Stop) as ctx:
            self.upgrade_to(self.core_b)
        self.assertIn("core not upgraded", str(ctx.exception))
        self.assertIn("hcore/counters.py", str(ctx.exception))
        self.assertEqual(uf.tree(self.root), before)

    def test_co05_mixed_copy_is_reported_then_updated(self):
        """CO-05: files of two history entries -> mixed; every file is pristine, so it is updated (CO-06)."""
        self.install(self.core_a)
        (self.project_core() / "MANIFEST.json").unlink()
        shutil.copy(str(self.core_b / "hcore" / "blocks.py"), str(self.project_core() / "hcore" / "blocks.py"))
        info = history.installed_core(self.project_core(), history.load(self.core_b))
        self.assertTrue(info["mixed"])
        plan = self.upgrade_to(self.core_b)
        self.assertEqual(plan["action"], "plan")

    def test_co06_one_copy_is_updated_with_the_blocks_of_every_profile(self):
        """CO-06: a single plan updates the copy and the blocks of all the active profiles, all or nothing."""
        research = self.tmp / "profiles-all"
        shutil.copytree(str(uf.PROFILES), str(research))
        both = ["software", "research"]
        plan = upgrade.build_plan(self.root, upgrade.SF, self.info, self.core_a, research, requested=both,
                                  interpreter=uf.INTERPRETER, now=uf.NOW, assets=uf.ASSETS)
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        self.assertTrue((self.root / ".claude/skills/factory-run/SKILL.md").exists())
        self.assertTrue((self.root / ".claude/skills/lab-run/SKILL.md").exists())
        (self.project_core() / "MANIFEST.json").unlink()
        newer = dict(self.info, describe="v-next")                  # the version header of every file changes
        plan = upgrade.build_plan(self.root, upgrade.SF, newer, self.core_b, research, interpreter=uf.INTERPRETER,
                                  now=uf.NOW, assets=uf.ASSETS)
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        changed = [c["file"] for c in plan["changes"]]
        self.assertIn(".claude/skills/factory-run/SKILL.md", changed)
        self.assertIn(".claude/skills/lab-run/SKILL.md", changed)
        self.assertIn(".claude/harness/hcore/blocks.py", changed)
        before = uf.tree(self.root)
        real = upgrade._write_file

        def failing(path, text):
            if Path(path).name == "SKILL.md" and Path(path).parent.name == "lab-run":
                raise OSError("injected")
            return real(path, text)
        upgrade._write_file = failing
        try:
            with self.assertRaises(OSError):
                upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        finally:
            upgrade._write_file = real
        self.assertEqual(uf.tree(self.root), before)                # nothing modified, stamp included
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        self.assertIn("v-next", uf.read(self.root, ".claude/skills/lab-run/SKILL.md"))
        self.assertIn("v-next", uf.read(self.root, ".claude/skills/factory-run/SKILL.md"))

    def test_co07_the_blocked_copy_message_names_the_upgrade(self):
        """CO-07 (EP-07): the message of a blocked installation gives /software-factory --upgrade."""
        dest = self.tmp / "blocked" / ".claude" / "harness"
        shutil.copytree(str(self.core_b), str(dest))
        (dest / "hcore" / "counters.py").write_bytes((dest / "hcore" / "counters.py").read_bytes() + b"#")
        res = H(["sf", "copy-core", "--root", str(self.tmp / "blocked")], self.tmp)
        self.assertEqual(res.returncode, 1)
        self.assertIn("/software-factory --upgrade", res.stderr)


class CurrentTreeTest(unittest.TestCase):
    def test_co01_the_current_tree_is_an_entry_of_history_json(self):
        """CO-01, CA-07, CA-29: a commit that touches skills/_core without `core history --rebuild` is caught here."""
        self.assertEqual(subprocess_check(helpers.CORE), 0, subprocess_check(helpers.CORE, True)[1])


def subprocess_check(core: Path, with_output: bool = False):
    res = helpers.subprocess.run([helpers.sys.executable, "-B", str(core / "harness.py"), "core", "history",
                                  "--check"], capture_output=True, text=True, encoding="utf-8")
    return (res.returncode, res.stdout + res.stderr) if with_output else res.returncode


if __name__ == "__main__":
    unittest.main()
