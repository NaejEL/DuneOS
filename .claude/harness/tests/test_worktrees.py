# Worktrees of the harness: WT-01 to WT-09 (and the worktree merge cleanup SM-06 / WT-07).
#
# WT-10 (README section "Coexistence avec vos worktrees") is documentation: checked by the grep of CA-39.

import json
import os
import unittest
from pathlib import Path

import helpers
import phase4
from hcore import runs, worktrees
from hcore.proc import Runner


class WorktreeTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.work, self.remote = phase4.project(self.tmp, {"SPEC-a.md": phase4.spec_text("PROPOSEE")})
        self.run = Runner(cwd=str(self.work))

    def create(self, slug="spec-a", spec="specs/SPEC-a.md"):
        return worktrees.create(self.run, self.work, slug, "main", spec, "test", "batch1")

    def test_wt02_creation_path_branch_and_base(self):
        """WT-02: <parent>/<repo>.harness-wt/<slug>, branch factory/<slug> from origin/<target>."""
        entry = self.create()
        expected = self.work.parent / ("work.harness-wt") / "spec-a"
        self.assertEqual(os.path.normcase(entry["path"]), os.path.normcase(str(expected)))
        self.assertEqual(entry["branch"], "factory/spec-a")
        self.assertEqual(entry["base"], "origin/main")
        self.assertTrue((expected / "README.md").is_file())
        self.assertEqual(helpers.git(expected, "rev-parse", "--abbrev-ref", "HEAD").strip(), "factory/spec-a")

    def test_wt02_an_existing_path_or_branch_is_never_reused(self):
        """WT-02: a second creation with the same slug gets a timestamp suffix."""
        first = self.create()
        second = self.create()
        self.assertNotEqual(first["path"], second["path"])
        self.assertNotEqual(first["branch"], second["branch"])
        self.assertRegex(second["branch"], r"^factory/spec-a-[0-9]{8}-[0-9]{6}$")

    def test_wt01_the_registry_is_common_to_all_worktrees_and_unversioned(self):
        """WT-01: <git-common-dir>/harness/worktrees.json with the documented fields; the same file seen from a worktree."""
        entry = self.create()
        common = worktrees.common_dir(self.run, self.work)
        data = json.loads((common / "harness" / "worktrees.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(data[0]), sorted(["path", "branch", "base", "spec", "created_by", "batch_id",
                                                  "created_at", "state"]))
        self.assertEqual(worktrees.common_dir(Runner(cwd=entry["path"]), entry["path"]), common)
        self.assertEqual(helpers.git(self.work, "status", "--porcelain").strip(), "")

    def test_wt03_wt04_a_worktree_of_the_user_is_never_touched(self):
        """WT-03, WT-04, CA-23: removing a worktree outside the registry -> exit 1 and the list is identical;
        no `prune` nor `remove --force` is ever run."""
        user = self.tmp / "user-wt"
        helpers.git(self.work, "worktree", "add", "-q", "-b", "user/topic", str(user))
        before = helpers.git(self.work, "worktree", "list", "--porcelain")
        env = phase4.env_for(self.tmp)
        res = helpers.harness(["worktree", "remove", "--root", str(self.work), "--path", str(user), "--merged"], self.work,
                              env=env)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("not a worktree created by the harness", res.stderr)
        self.assertEqual(helpers.git(self.work, "worktree", "list", "--porcelain"), before)
        self.assertTrue(user.is_dir())
        # A registered one is created and removed through the CLI; the git journal holds neither forbidden command.
        res = helpers.harness(["worktree", "create", "--root", str(self.work), "--slug", "x", "--target", "main"], self.work,
                              env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        path = json.loads(res.stdout)["path"]
        res = helpers.harness(["worktree", "remove", "--root", str(self.work), "--path", path, "--merged"], self.work, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        joined = [" ".join(c) for c in phase4.git_calls(self.tmp)]
        self.assertFalse(any("worktree prune" in c or "remove --force" in c or "-f" in c.split() and "worktree" in c
                             for c in joined))
        self.assertTrue(any(c.startswith("worktree remove") for c in joined))

    def test_wt03_registered_but_not_listed_is_refused(self):
        """WT-03: the registry AND `git worktree list` must agree, branch included."""
        entry = self.create()
        helpers.git(self.work, "worktree", "remove", "--force", entry["path"])          # removed behind the registry's back
        self.assertIsNone(worktrees.entry_of(self.run, self.work, entry["path"]))
        with self.assertRaises(worktrees.WorktreeError):
            worktrees.remove(self.run, self.work, entry["path"], True)

    def test_wt05_a_spec_that_differs_from_the_base_is_handed_off(self):
        """WT-05, CA-25: untracked or modified spec -> copied into the worktree, original moved to .harness/runs/handoff/."""
        phase4.write_spec(self.work, "SPEC-new.md", phase4.spec_text("PROPOSEE", "New"))
        entry = self.create("new", "specs/SPEC-new.md")
        moved = worktrees.transfer_spec(self.run, self.work, Path(entry["path"]),
                                        "specs/SPEC-new.md", "main")
        self.assertTrue(moved["transferred"])
        self.assertTrue((Path(entry["path"]) / "specs" / "SPEC-new.md").is_file())
        self.assertFalse((self.work / "specs" / "SPEC-new.md").exists())
        self.assertEqual(len(list((self.work / ".harness" / "runs" / "handoff").glob("*-SPEC-new.md"))), 1)
        # A spec identical to the base stays where it is.
        same = worktrees.transfer_spec(self.run, self.work, Path(entry["path"]), "specs/SPEC-a.md", "main")
        self.assertFalse(same["transferred"])
        self.assertTrue((self.work / "specs" / "SPEC-a.md").exists())

    def test_wt05_the_cli_transfers_only_into_a_registered_worktree(self):
        """WT-05, WT-03: `worktree transfer` hands the spec off; an unregistered path is refused."""
        phase4.write_spec(self.work, "SPEC-new.md", phase4.spec_text("PROPOSEE", "New"))
        entry = self.create("new", "specs/SPEC-new.md")
        base = ["worktree", "transfer", "--root", str(self.work), "--spec", "specs/SPEC-new.md", "--target", "main"]
        res = helpers.harness(base + ["--path", entry["path"]], self.work)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(json.loads(res.stdout)["transferred"])
        refused = helpers.harness(base + ["--path", str(self.tmp)], self.work)
        self.assertEqual(refused.returncode, 1)

    def test_wt06_wt07_collect_then_remove_after_the_merge(self):
        """WT-06, WT-07, CA-15, CA-25: logs copied under wt-<slug>, findings merged, worktree and local branch removed,
        registry empty; without a merge the worktree is kept."""
        entry = self.create()
        wt = Path(entry["path"])
        (wt / ".harness" / "runs").mkdir(parents=True, exist_ok=True)
        (wt / ".harness" / "runs" / "factory-run-1.jsonl").write_text("{}\n", encoding="utf-8")
        (wt / ".claude").mkdir(exist_ok=True)
        (wt / ".claude" / "findings.jsonl").write_text(json.dumps({"key": "a::b", "status": "FIXED", "source": "ship-pr",
                                                                    "run": "r"}) + "\n", encoding="utf-8")
        kept = worktrees.remove(self.run, self.work, entry["path"], False)
        self.assertFalse(kept["removed"])
        self.assertTrue(wt.is_dir())
        done = worktrees.remove(self.run, self.work, entry["path"], True)
        self.assertTrue(done["removed"])
        self.assertTrue((self.work / ".harness" / "runs" / "wt-spec-a" / "factory-run-1.jsonl").is_file())
        self.assertIn("a::b", (self.work / ".claude" / "findings.jsonl").read_text(encoding="utf-8"))
        self.assertFalse(wt.exists())
        self.assertNotIn("factory/spec-a", helpers.git(self.work, "branch", "--list"))
        self.assertEqual(worktrees.load(worktrees.common_dir(self.run, self.work)), [])
        # idempotent publication
        worktrees.collect(self.work, wt, "spec-a")

    def test_wt07_a_dirty_worktree_is_kept_and_reported(self):
        """WT-07: never forced; a dirty worktree is kept."""
        entry = self.create()
        (Path(entry["path"]) / "wip.txt").write_text("x", encoding="utf-8")
        result = worktrees.remove(self.run, self.work, entry["path"], True)
        self.assertEqual((result["removed"], result["reason"]), (False, "the worktree is dirty: kept"))

    def test_wt09_a_base_without_the_harness_files_is_a_gate_stop(self):
        """WT-09, CA-25: .claude/harness/ or .claude/settings.json missing from origin/<target> -> stop `gate`."""
        helpers.git(self.work, "rm", "-q", "-r", ".claude/harness")
        helpers.git(self.work, "commit", "-q", "-m", "drop the core")
        helpers.git(self.work, "push", "-q", "origin", "main")
        with self.assertRaises(worktrees.BaseMissing) as ctx:
            self.create()
        self.assertIn("commit the harness files first", str(ctx.exception))
        res = helpers.harness(["worktree", "create", "--root", str(self.work), "--slug", "x", "--target", "main"], self.work)
        self.assertEqual(res.returncode, 1)
        self.assertIn("— gate", sorted((self.work / ".harness" / "runs").glob("STOP-*.md"))[0].read_text(encoding="utf-8"))

    def test_wt08_the_worktree_option_is_an_option_of_factory_run(self):
        """WT-08: --worktree is read out of the arguments of factory-run (the cycle itself is driven by the skill)."""
        from hcore import chain
        rest, options = chain.extract_options("specs/SPEC-a.md --worktree --ship main")
        self.assertEqual((rest, options["worktree"], options["ship"]), ("specs/SPEC-a.md", True, "main"))


if __name__ == "__main__":
    unittest.main()
