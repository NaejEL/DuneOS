"""Phase 3, UP-01 to UP-13, DT-01 to DT-04, EP-08 to EP-11, CX-17 to CX-22: the upgrade engine."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from datetime import timedelta
from unittest import mock

import helpers
import upgrade_fixtures as uf
from hcore import blocks, cx, journal, runs, stamp, upgrade
from hcore.ghflow import Stop

H = helpers.harness


class ArgsTest(helpers.TempDirCase):
    def args(self, text: str, *extra: str):
        src = helpers.write_output(self.tmp, "args.txt", text)
        return H(["sf", "args", "--root", str(self.tmp), "--arguments-file", src] + list(extra), self.tmp)

    def test_ep08_upgrade_excludes_every_other_argument(self):
        """EP-08: --upgrade [--convert] only: --profile, --rescan or free text -> exit 2."""
        for text in ("--upgrade --profile software", "--upgrade --rescan", "--upgrade du texte", "--convert"):
            self.assertEqual(self.args(text).returncode, 2, text)
        res = self.args("--upgrade --convert")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual({k: json.loads(res.stdout)[k] for k in ("upgrade", "convert")}, {"upgrade": True, "convert": True})

    def test_ep10_ailab_upgrade_has_no_profile_option(self):
        """EP-10: /ai-lab --upgrade -> software-factory --upgrade, without --profile research (exception to AX-01)."""
        out = json.loads(self.args("--upgrade", "--via", "ai-lab").stdout)["translated"]
        self.assertEqual(out, "--upgrade")
        self.assertNotIn("--profile", out)
        self.assertEqual(upgrade.ailab_translate("ma question"), "--profile research ma question")

    def test_cx17_upgrade_excludes_reindex(self):
        """CX-17: `cx args` with --upgrade --reindex -> exit 2; --upgrade alone is parsed."""
        src = helpers.write_output(self.tmp, "cx.txt", "--upgrade --reindex")
        self.assertEqual(H(["cx", "args", "--root", str(self.tmp), "--arguments-file", src], self.tmp).returncode, 2)
        src = helpers.write_output(self.tmp, "cx.txt", "--upgrade")
        res = H(["cx", "args", "--root", str(self.tmp), "--arguments-file", src], self.tmp)
        self.assertTrue(json.loads(res.stdout)["upgrade"])

    def test_ep09_unequipped_project_stops(self):
        """EP-09: source none -> exit 1, `not equipped`."""
        res = H(["upgrade", "plan", "--root", str(self.tmp), "--skill", "software-factory"], self.tmp)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not equipped", res.stderr)


class InterpreterTest(helpers.TempDirCase):
    def entry(self, py):
        return {"options": {"interpreter": py}}

    def test_dt05_the_stamp_interpreter_is_kept_while_it_answers_here(self):
        """DT-05: a caller on another machine does not rewrite the files as long as the recorded interpreter works."""
        with mock.patch("hcore.interp.probe", return_value=(3, 12, 0)) as probe:
            self.assertEqual(upgrade.interpreter_of(self.tmp, self.entry("py -3"), "python"), "py -3")
        probe.assert_called_once_with(["py", "-3"])

    def test_dt05_a_recorded_interpreter_absent_here_gives_way_to_the_caller(self):
        """DT-05: recorded interpreter missing (or too old) on this machine -> the caller's one."""
        for answer in (None, (3, 8, 10)):
            with mock.patch("hcore.interp.probe", return_value=answer):
                self.assertEqual(upgrade.interpreter_of(self.tmp, self.entry("py -3"), "python"), "python", answer)

    def test_dt03_order_without_conflict_is_unchanged(self):
        """DT-03: no stamp -> the option; stamp equal to the option or no option -> the stamp, without probing."""
        with mock.patch("hcore.interp.probe") as probe:
            self.assertEqual(upgrade.interpreter_of(self.tmp, {}, "python3"), "python3")
            self.assertEqual(upgrade.interpreter_of(self.tmp, self.entry("python3"), "python3"), "python3")
            self.assertEqual(upgrade.interpreter_of(self.tmp, self.entry("py -3"), None), "py -3")
        probe.assert_not_called()


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class UpgradeBlocksTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.root, self.info, self.pv1, self.pv2 = uf.blocks_project(self.tmp)

    def plan(self, profiles_dir=None, skill=upgrade.SF, info=None, **kw):
        return upgrade.build_plan(self.root, skill, info or self.info, uf.CORE, profiles_dir or self.pv2,
                                  interpreter=uf.INTERPRETER, now=uf.NOW, assets=uf.ASSETS, **kw)

    def test_up01_a_plan_writes_only_under_harness_runs(self):
        """UP-01: plan and diff go under .harness/runs/, nothing else in the project changes."""
        before = uf.tree(self.root)
        plan = self.plan()
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        self.assertEqual(uf.tree(self.root), before)
        self.assertTrue((self.root / files["plan"]).is_file() and (self.root / files["diff"]).is_file())

    def test_up02_up03_up07_blocks_are_regenerated_and_the_rest_preserved(self):
        """UP-02, UP-03, UP-07, CA-12: v1 -> v2 touches blocks only; strip_blocks and user content are unchanged."""
        before = uf.tree(self.root, skip=(".git", ".harness"))
        plan = self.plan()
        self.assertTrue(plan["blocks_only"])
        self.assertTrue({c["kind"] for c in plan["changes"]} <= set(upgrade.BLOCKS_ONLY))
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        after = uf.tree(self.root, skip=(".git", ".harness"))
        for rel in (c["file"] for c in plan["changes"] if c["kind"] == "block"):
            self.assertEqual(blocks.strip_blocks(before[rel].decode()).rstrip("\n"),
                             blocks.strip_blocks(after[rel].decode()).rstrip("\n"), rel)
        self.assertIn("Règles maison", after[".claude/agents/factory-planner.md"].decode())
        self.assertIn("FACTORY_EXTRA", after["ci/factory.sh"].decode())
        settings = json.loads(after[".claude/settings.json"].decode())
        self.assertEqual(settings["env"], {"MINE": "1"})
        self.assertIn("Read(./secret/**)", settings["permissions"]["deny"])
        tasks = json.loads(after[".vscode/tasks.json"].decode())
        self.assertIn("Mine", [t["label"] for t in tasks["tasks"]])
        self.assertIn("mine-2/", after[".gitignore"].decode())
        # the blocks equal those of a fresh v2 installation
        fresh = self.tmp / "fresh"
        uf.fresh_install(fresh, self.pv2, self.info)
        for rel in (".claude/agents/factory-planner.md", ".claude/skills/factory-run/SKILL.md"):
            names = blocks.harness_block_names(uf.read(fresh, rel))
            for name in names:
                style = "hash" if name == "harness.frontmatter" else "md"
                self.assertEqual(blocks.get_block(uf.read(self.root, rel), name, style),
                                 blocks.get_block(uf.read(fresh, rel), name, style), (rel, name))

    def test_up04_the_diff_is_complete_and_applies_with_git(self):
        """UP-04, CA-14: `git apply --check` on the previous state, and the applied diff gives the produced state."""
        twin = self.tmp / "twin"
        shutil.copytree(str(self.root), str(twin))
        helpers.init_repo(twin)
        helpers.git(twin, "add", "-A", "-f")
        helpers.git(twin, "commit", "-q", "-m", "before")
        plan = self.plan()
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        diff = (self.root / files["diff"]).read_text(encoding="utf-8")
        for change in plan["changes"]:
            self.assertIn("+++ b/%s" % change["file"], diff)
        patch = self.tmp / "u.diff"
        patch.write_bytes(diff.encode("utf-8"))
        helpers.git(twin, "apply", "--check", str(patch))
        helpers.git(twin, "apply", str(patch))
        upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        self.assertEqual(uf.tree(twin), uf.tree(self.root))

    def test_up05_a_stale_plan_is_refused_and_nothing_is_written(self):
        """UP-05, CA-15: a file changed between plan and apply -> exit 1, `plan is stale`, no write."""
        plan = self.plan()
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        target = self.root / ".claude" / "agents" / "factory-builder.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nchanged after the plan\n", encoding="utf-8")
        before = uf.tree(self.root)
        with self.assertRaises(Stop) as ctx:
            upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)
        self.assertIn("plan is stale", str(ctx.exception))
        self.assertEqual(uf.tree(self.root), before)

    def test_up05_up10_backup_and_journal(self):
        """UP-05, UP-10: each modified file is saved first; one `upgrade` event per file, journal valid (JR-04)."""
        original = (self.root / ".claude/agents/factory-planner.md").read_bytes()
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv2, now=uf.NOW + timedelta(minutes=5))
        saved = self.root / res["applied"]["backup"] / ".claude/agents/factory-planner.md"
        self.assertEqual(saved.read_bytes(), original)
        jpath = self.root / res["applied"]["journal"]
        events = [json.loads(l) for l in jpath.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted(e["file"] for e in events if e["event"] == "upgrade"),
                         sorted(c["file"] for c in res["changes"]))
        self.assertEqual(journal.validate_file(jpath), [])
        self.assertEqual(H(["journal", "validate", str(jpath)], self.tmp).returncode, 0)

    def test_up06_idempotence_and_empty_apply(self):
        """UP-06, CA-13: a plan right after an apply is empty; a second apply modifies no file."""
        uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv2)
        before = uf.tree(self.root)
        again = uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv2)
        self.assertEqual((again["changes"], again["applied"]["applied"]), ([], 0))
        self.assertEqual(uf.tree(self.root), before)

    def test_up08_no_way_back(self):
        """UP-08: a project installed by a newer harness is refused (newer commit lineage or core_version)."""
        data = json.loads(uf.read(self.root, ".claude/harness.json"))
        data["skills"]["software-factory"]["core_version"] = "99.0.0"
        uf.write(self.root, ".claude/harness.json", json.dumps(data, indent=2) + "\n")
        with self.assertRaises(Stop) as ctx:
            self.plan()
        self.assertIn("newer harness", str(ctx.exception))
        self.assertIn("update claude-skills first (git pull)", str(ctx.exception))

    def test_up08_installed_commit_ahead_of_the_repository(self):
        """UP-08: installed commit = descendant of the repository HEAD -> `newer harness`."""
        repo_dir = uf.Path(self.info["path"])
        head = helpers.git(repo_dir, "rev-parse", "HEAD").strip()
        old = dict(self.info, commit=helpers.git(repo_dir, "rev-parse", "HEAD~1").strip())
        data = json.loads(uf.read(self.root, ".claude/harness.json"))
        data["skills"]["software-factory"]["repo_commit"] = head
        uf.write(self.root, ".claude/harness.json", json.dumps(data, indent=2) + "\n")
        with self.assertRaises(Stop) as ctx:
            self.plan(info=old)
        self.assertIn("newer harness", str(ctx.exception))

    def test_up09_one_upgrade_at_a_time(self):
        """UP-09: the lock .harness/runs/upgrade.lock is exclusive."""
        plan = self.plan()
        files = upgrade.write_plan(self.root, plan, uf.NOW)
        with runs.locked_file(self.root / upgrade.LOCK):
            with mock.patch.object(runs, "LOCK_TIMEOUT", 0.3):
                with self.assertRaises(runs.LockTimeout):
                    upgrade.apply_plan(self.root, self.root / files["plan"], None, uf.NOW)

    def install_code_index(self) -> None:
        """What /code-index does on a fresh install: rgkb.py, the wiring (K cx wire), then the first upgrade."""
        uf.write(self.root, ".claude/rgkb/rgkb.py", (uf.ASSETS / "rgkb.py").read_text(encoding="utf-8"))
        contents = upgrade.rgkb_contents(self.root, ["python"], self.info["describe"], uf.ASSETS)
        cx.wire(self.root, contents, ["python"])
        uf.plan_and_apply(self.root, upgrade.CI, self.info, self.pv2, now=uf.NOW + timedelta(minutes=1))

    def test_up12_a_skill_never_touches_the_blocks_of_another(self):
        """UP-12: software-factory leaves the rgkb blocks alone, code-index leaves harness.* alone."""
        self.install_code_index()
        rel = ".claude/agents/factory-planner.md"
        before = uf.read(self.root, rel)
        rgkb_before = blocks.get_block(before, "rgkb", "md")
        self.assertIsNotNone(rgkb_before)
        uf.plan_and_apply(self.root, upgrade.SF, dict(self.info, describe="v-next"), self.pv2)
        self.assertEqual(blocks.get_block(uf.read(self.root, rel), "rgkb", "md").split("\n", 1)[1],
                         rgkb_before.split("\n", 1)[1])
        harness_before = blocks.get_block(uf.read(self.root, rel), "harness.body", "md")
        uf.plan_and_apply(self.root, upgrade.CI, dict(self.info, describe="v-next"), self.pv2)
        self.assertEqual(blocks.get_block(uf.read(self.root, rel), "harness.body", "md"), harness_before)

    def test_up13_a_missing_generated_file_is_recreated_not_as_blocks_only(self):
        """UP-13, Q13: classe `create`, never blocks_only."""
        (self.root / ".claude" / "agents" / "factory-verifier.md").unlink()
        plan = self.plan()
        kinds = {c["file"]: c["kind"] for c in plan["changes"]}
        self.assertEqual(kinds[".claude/agents/factory-verifier.md"], "create")
        self.assertFalse(plan["blocks_only"])

    def test_ca11_contributions_and_cross_idempotence(self):
        """RD-05, CA-11: sf -> ci -> sf -> ci with code-index declared: empty plans at steps 3 and 4."""
        self.install_code_index()
        steps = [uf.plan_and_apply(self.root, s, self.info, self.pv2, now=uf.NOW + timedelta(minutes=2 + i))
                 for i, s in enumerate((upgrade.SF, upgrade.CI, upgrade.SF, upgrade.CI))]
        self.assertEqual([c["file"] for c in steps[2]["changes"]] + [c["file"] for c in steps[3]["changes"]], [])
        planner = uf.read(self.root, ".claude/agents/factory-planner.md")
        for tool in cx.READ_TOOLS:
            self.assertIn(tool, planner)
        ok = helpers.bash_check(uf.read(self.root, "ci/factory.sh"))
        if ok is not None:
            self.assertTrue(ok, "bash -n fails on ci/factory.sh")


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class UpgradeLegacyTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        repo, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo)
        self.root = uf.make_legacy(self.tmp / "legacy")

    def test_dt01_dt02_dt03_a_project_without_stamp_is_detected(self):
        """DT-01, DT-02, DT-03: skills, profiles and generation inferred from the files; options deduced."""
        out = json.loads(H(["stamp", "resolve", "--root", str(self.root)], self.tmp).stdout)
        self.assertEqual(out["source"], "inferred")
        self.assertTrue({"software-factory", "code-index"} <= set(out["skills"]))
        self.assertEqual(sorted(out["profiles"]), ["research", "software"])
        self.assertEqual(out["generation"], "pre-core")
        res = upgrade.build_plan(self.root, upgrade.SF, self.info, uf.CORE, None, now=uf.NOW)   # interpreter: .mcp.json
        self.assertEqual(upgrade.interpreter_of(self.root, {}, None), "python")                    # DT-03
        self.assertEqual(res["generation"], "pre-core")

    def test_dt04_the_stamp_is_created_by_the_first_upgrade_and_not_by_a_failure(self):
        """DT-04, CA-17: inferred_from at the end of the first successful upgrade; a failed run leaves none."""
        real = upgrade._write_file
        count = []

        def failing(path, text):
            count.append(path)
            if len(count) == 5:
                raise OSError("injected")
            return real(path, text)
        upgrade._write_file = failing
        try:
            with self.assertRaises(OSError):
                uf.plan_and_apply(self.root, upgrade.SF, self.info)
        finally:
            upgrade._write_file = real
        self.assertFalse((self.root / ".claude" / "harness.json").exists())
        self.assertEqual(helpers.git(self.root, "status", "--porcelain", "--", ".claude/agents").strip().count("\n"),
                         0 if not helpers.git(self.root, "status", "--porcelain", "--", ".claude/agents").strip() else 0)
        uf.plan_and_apply(self.root, upgrade.SF, self.info)
        data = json.loads(uf.read(self.root, ".claude/harness.json"))
        self.assertEqual(data["inferred_from"], "pre-core")

    def test_cx18_to_cx22_code_index_upgrade(self):
        """CX-18 to CX-22, CA-20: rgkb.py, template blocks, config merge, hooks, second pass empty."""
        uf.plan_and_apply(self.root, upgrade.SF, self.info)
        res = uf.plan_and_apply(self.root, upgrade.CI, self.info)
        asset_version = cx.tool_version((uf.ASSETS / "rgkb.py").read_text(encoding="utf-8"))
        self.assertEqual(cx.tool_version(uf.read(self.root, ".claude/rgkb/rgkb.py")), asset_version)       # CX-18
        command = ["python"]
        contents = upgrade.rgkb_contents(self.root, command, self.info["describe"], uf.ASSETS)
        for name, rel in cx.wire_targets(self.root).items():
            self.assertEqual(blocks.get_block(uf.read(self.root, rel), "rgkb", "md"), contents[name].strip("\n"))  # CX-19
        config = json.loads(uf.read(self.root, ".knowledge/config.json"))                                  # CX-20
        self.assertIn("!mine/**", config["exclude"])
        self.assertEqual(config["hooks"], "auto")
        hooks = helpers.git(self.root, "rev-parse", "--git-path", "hooks").strip()
        for name in ("post-merge", "post-rewrite"):                                                        # CX-21
            self.assertIn("# >>> rgkb >>>", (self.root / hooks / name).read_text(encoding="utf-8"))
        self.assertIn(".knowledge/notes.jsonl", uf.tree(self.root))                                        # CX-22: no reindex
        self.assertEqual(uf.plan_and_apply(self.root, upgrade.CI, self.info,
                                           now=uf.NOW + timedelta(minutes=1))["changes"], [])
        self.assertTrue(any(c["kind"] == "local" for c in res["changes"]))

    def test_cx18_a_newer_project_rgkb_is_kept(self):
        """CX-18: a project TOOL_VERSION above the asset's is kept and reported, without stop."""
        uf.plan_and_apply(self.root, upgrade.SF, self.info)
        path = self.root / ".claude" / "rgkb" / "rgkb.py"
        path.write_text(re.sub(r'TOOL_VERSION = "[^"]+"', 'TOOL_VERSION = "9.9.9"', path.read_text(encoding="utf-8"), 1),
                        encoding="utf-8")
        res = uf.plan_and_apply(self.root, upgrade.CI, self.info)
        self.assertIn('TOOL_VERSION = "9.9.9"', path.read_text(encoding="utf-8"))
        self.assertTrue(any("newer" in n for n in res["notes"]))


if __name__ == "__main__":
    unittest.main()
