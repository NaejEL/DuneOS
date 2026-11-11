# Profile stamp of .claude/harness.json (HJ-01 to HJ-06).

import json
import shutil
import unittest
from pathlib import Path

import helpers
import phase2
from hcore import blocks, profiles, stamp


def h(root, *args):
    return helpers.harness(list(args), root)


class StampTest(helpers.TempDirCase):
    def stamp_file(self):
        return self.tmp / ".claude" / "harness.json"

    def test_hj01_only_the_profiles_key_is_written_other_keys_kept(self):
        """HJ-01, CA-11: `stamp add` writes the `profiles` list ({name, added, by}) and keeps autonomy and
        every other key in their order; strict JSON, indent 2, LF."""
        self.stamp_file().parent.mkdir()
        self.stamp_file().write_text('{"autonomy": "gate", "x": 1}', encoding="utf-8")
        res = h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "software", "--by", "scout",
                "--date", "2026-10-31")
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(self.stamp_file().read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["autonomy", "x", "profiles"])
        self.assertEqual(data["profiles"], [{"name": "software", "added": "2026-10-31", "by": "scout"}])
        self.assertTrue(self.stamp_file().read_text(encoding="utf-8").startswith('{\n  "autonomy"'))

    def test_hj02_unreadable_json_or_bad_profiles_key_is_refused_file_untouched(self):
        """HJ-02, CA-11: unreadable JSON, or `profiles` that is not a list -> exit 2, file not rewritten."""
        self.stamp_file().parent.mkdir()
        for bad in ("{not json", '{"profiles": "software"}', '{"profiles": [1]}'):
            self.stamp_file().write_text(bad, encoding="utf-8")
            res = h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "research", "--by", "forced")
            self.assertEqual(res.returncode, 2, bad)
            self.assertEqual(self.stamp_file().read_text(encoding="utf-8"), bad)

    def test_hj03_the_core_only_adds_never_duplicates_never_removes(self):
        """HJ-03, CA-11: a profile already present keeps its `added`; a second write without addition leaves
        the file identical byte for byte; there is no `stamp remove`."""
        h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "software", "--by", "scout",
          "--date", "2026-10-01")
        first = self.stamp_file().read_bytes()
        res = h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "software", "--by", "forced",
                "--date", "2026-11-01")
        self.assertEqual(json.loads(res.stdout), {"added": [], "kept": ["software"], "written": False})
        self.assertEqual(self.stamp_file().read_bytes(), first)
        h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "research", "--by", "scout",
          "--date", "2026-11-02")
        data = json.loads(self.stamp_file().read_text(encoding="utf-8"))["profiles"]
        self.assertEqual([(p["name"], p["added"]) for p in data], [("software", "2026-10-01"), ("research", "2026-11-02")])
        self.assertEqual(h(self.tmp, "stamp", "remove", "--root", str(self.tmp)).returncode, 2)
        self.assertEqual(h(self.tmp, "stamp", "add", "--root", str(self.tmp), "--profiles", "zzz", "--by",
                           "forced").returncode, 2)

    def test_hj04_resolve_stamp_then_inferred_then_none(self):
        """HJ-04, CA-11: stamp first; else the files of an installation before Phase 2 (factory-run or
        factory agents -> software, lab-run or lab agents -> research); else none."""
        self.assertEqual(stamp.resolve(self.tmp), {"source": "none", "profiles": []})
        skill = self.tmp / ".claude" / "skills" / "factory-run" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("x", encoding="utf-8")
        self.assertEqual(stamp.resolve(self.tmp), {"source": "inferred", "profiles": ["software"]})
        agent = self.tmp / ".claude" / "agents" / "lab-director.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("x", encoding="utf-8")
        self.assertEqual(stamp.resolve(self.tmp)["profiles"], ["research", "software"])
        stamp.add(self.tmp, ["research"], "inferred")
        resolved = json.loads(h(self.tmp, "stamp", "resolve", "--root", str(self.tmp)).stdout)
        self.assertEqual({k: resolved[k] for k in ("source", "profiles")},      # DT-01: + skills, generation
                         {"source": "stamp", "profiles": ["research"]})

    def test_hj04_resolve_on_the_toy_project_with_an_older_factory(self):
        """HJ-04, CA-11: inferred/software on a toy project equipped by a pre-Phase 2 factory-run."""
        if not helpers.HAS_FIXTURES:
            self.skipTest(helpers.SKIP_FIXTURES)
        shutil.copytree(str(helpers.FIXTURES / "toy-project"), str(self.tmp / "p"))
        skill = self.tmp / "p" / ".claude" / "skills" / "factory-run" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("x", encoding="utf-8")
        self.assertEqual(stamp.resolve(self.tmp / "p"), {"source": "inferred", "profiles": ["software"]})

    def test_hj05_the_stamp_is_written_last_a_blocked_copy_leaves_it_unchanged(self):
        """HJ-05, EP-07: a blocked copy-core exits 1 before anything else; the stamp does not exist."""
        other = self.tmp / ".claude" / "harness"
        shutil.copytree(str(helpers.CORE), str(other), ignore=shutil.ignore_patterns("__pycache__", "tests"))
        (other / "hcore" / "relaunch.py").write_text("# different\n", encoding="utf-8")
        res = h(self.tmp, "sf", "copy-core", "--root", str(self.tmp))
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertFalse(self.stamp_file().exists())

    def test_hj06_cumulated_profiles_merge_by_union(self):
        """HJ-06, EP-05, CA-12: roles and agent files are the union (never shared), deny rules, tasks and
        .gitignore lines are the additive union of the profiles."""
        soft, res = profiles.get("software"), profiles.get("research")
        files = [r["agent_file"] for p in (soft, res) for r in p["roles"]]
        self.assertEqual(len(files), len(set(files)))
        merged = profiles.merged([soft, res], ["python"], self.tmp, "pytest")
        self.assertIn("Read(./.venv/**)", merged["deny"])
        self.assertIn("Read(./experiments/results/**)", merged["deny"])
        self.assertEqual(merged["gitignore"], ["factory-logs/", ".harness/runs/", ".harness/tmp/", "lab-logs/", "experiments/results/"])
        self.assertEqual({i["id"] for i in merged["inputs"]}, {"factorySpec", "labProtocol"})
        labels = [t["label"] for t in merged["tasks"]]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertIn("Lab: tests unitaires", labels)
        # EP-05: an apply with the second profile leaves the settings of the first one.
        self.assertEqual(h(self.tmp, "sf", "apply", "--root", str(self.tmp), "--stacks", "python", "--profiles",
                           "software").returncode, 0)
        before = json.loads((self.tmp / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(h(self.tmp, "sf", "apply", "--root", str(self.tmp), "--stacks", "python", "--profiles",
                           "software;research").returncode, 0)
        after = json.loads((self.tmp / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(after["permissions"]["deny"][:len(before["permissions"]["deny"])],
                         before["permissions"]["deny"])
        self.assertIn("Read(./lab-logs/**)", after["permissions"]["deny"])

    def test_ep06_a_profile_block_is_added_and_the_text_outside_is_unchanged(self):
        """EP-06, EP-05, CA-12: the orchestrator gets one harness.profile.<name> block per profile; the
        text outside a block stays byte for byte equal when a block is added or replaced."""
        body = "# factory-run\n\ntexte commun avec $ARGUMENTS et `backticks`\n"
        first = blocks.upsert_block(body, "harness.profile.software", "bloc logiciel", "md")
        second = blocks.upsert_block(first, "harness.profile.research", "bloc recherche", "md")
        self.assertTrue(second.startswith(body))
        self.assertIn("<!-- harness.profile.research:debut -->", second)
        again = blocks.upsert_block(second, "harness.profile.research", "bloc recherche v2", "md")
        self.assertEqual(again, second.replace("bloc recherche", "bloc recherche v2"))
        self.assertTrue(again.startswith(body))


if __name__ == "__main__":
    unittest.main()
