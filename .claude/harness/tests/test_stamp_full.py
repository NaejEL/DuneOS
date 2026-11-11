"""Phase 3, HJ-07 to HJ-15: the complete stamp .claude/harness.json."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta

import helpers
import upgrade_fixtures as uf
from hcore import stamp, upgrade
from hcore.ghflow import Stop

H = helpers.harness


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class StampFullTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        repo, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo)
        self.pv1 = uf.make_profiles(self.tmp)
        self.root = self.tmp / "proj"

    def stamp(self) -> dict:
        return json.loads(uf.read(self.root, ".claude/harness.json"))

    def test_hj07_hj10_schema_and_skill_entry(self):
        """HJ-07, HJ-10: schema_version, autonomy, profiles, skills.<skill> with repo version, options, params, files."""
        uf.fresh_install(self.root, self.pv1, self.info)
        data = self.stamp()
        self.assertEqual(list(data)[:4], ["schema_version", "autonomy", "profiles", "skills"])
        self.assertEqual(data["schema_version"], 1)
        entry = data["skills"]["software-factory"]
        for key in ("repo_commit", "repo_describe", "repo_dirty", "core_version", "installed_at", "updated_at",
                    "options", "params", "files"):
            self.assertIn(key, entry)
        self.assertEqual(entry["repo_commit"], self.info["commit"])
        self.assertEqual(entry["files"][".claude/agents/factory-planner.md"]["kind"], "generated")
        self.assertIn("harness.body", entry["files"][".claude/agents/factory-planner.md"]["blocks"])
        self.assertEqual(entry["options"]["interpreter"], uf.INTERPRETER)

    def test_hj09_autonomy_default_is_auto_and_an_existing_value_is_kept(self):
        """HJ-09, HJ-08: absent -> auto; an existing value, unknown keys and their order are never modified."""
        uf.fresh_install(self.root, self.pv1, self.info)
        self.assertEqual(self.stamp()["autonomy"], "auto")
        other = self.tmp / "p2"
        uf.write(other, ".claude/harness.json", json.dumps(
            {"profiles": [{"name": "software", "added": "2026-10-01", "by": "scout"}], "autonomy": "gate", "x": 1},
            indent=2) + "\n")
        uf.plan_and_apply(other, upgrade.SF, self.info, self.pv1)
        data = json.loads(uf.read(other, ".claude/harness.json"))
        self.assertEqual(list(data)[:3], ["profiles", "autonomy", "x"])       # HJ-08: original order, new keys after
        self.assertEqual((data["autonomy"], data["x"]), ("gate", 1))
        self.assertEqual(data["schema_version"], 1)                           # MI-03: phase 2 stamp -> schema 1
        self.assertEqual(data["profiles"][0]["added"], "2026-10-01")

    def test_hj11_dates_are_iso_and_an_empty_apply_leaves_the_stamp_identical(self):
        """HJ-11: ISO 8601 with offset, to the second; a second apply keeps the stamp byte for byte."""
        uf.fresh_install(self.root, self.pv1, self.info)
        first = (self.root / ".claude" / "harness.json").read_bytes()
        entry = self.stamp()["skills"]["software-factory"]
        self.assertRegex(entry["installed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        later = uf.NOW + timedelta(days=3)
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv1, now=later)
        self.assertEqual(res["changes"], [])
        self.assertEqual((self.root / ".claude" / "harness.json").read_bytes(), first)

    def test_hj12_the_stamp_holds_nothing_machine_specific(self):
        """HJ-12, HJ-15: `stamp validate` refuses absolute paths, rg_path and an unknown schema (exit 2)."""
        uf.fresh_install(self.root, self.pv1, self.info)
        good = self.root / ".claude" / "harness.json"
        self.assertEqual(H(["stamp", "validate", str(good)], self.tmp).returncode, 0)
        for bad in ({"schema_version": 1, "where": "C:\\Users\\me\\x"}, {"schema_version": 1, "where": "/home/me"},
                    {"schema_version": 1, "skills": {"a": {"rg_path": "x"}}}, {"schema_version": 7}):
            path = self.tmp / "bad.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            self.assertEqual(H(["stamp", "validate", str(path)], self.tmp).returncode, 2, bad)
        shown = json.loads(H(["stamp", "show", "--root", str(self.root)], self.tmp).stdout)
        self.assertTrue(shown["present"])

    def test_hj13_migrations_are_append_only(self):
        """HJ-13: {id, at, sources: [{path, sha256}]}, appended, never rewritten."""
        uf.fresh_install(self.root, self.pv1, self.info)
        reg = '{"key": "a.py::x", "file": "a.py", "line": 1, "title": "X", "verdict": "FIXED"}\n'
        uf.write(self.root, ".harness/runs/ship-pr-20261001-101010.registry.jsonl", reg)
        uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv1)
        first = self.stamp()["migrations"]
        uf.write(self.root, ".harness/runs/ship-pr-20261002-101010.registry.jsonl", reg.replace("a.py", "b.py"))
        uf.plan_and_apply(self.root, upgrade.SF, self.info, self.pv1)
        second = self.stamp()["migrations"]
        self.assertEqual(second[0], first[0])
        self.assertEqual(len(second), 2)
        self.assertEqual(set(second[0]), {"id", "at", "sources"})

    def test_hj14_the_stamp_is_written_last_and_a_failure_leaves_none(self):
        """HJ-14: a failure before the end leaves the stamp absent; the next run succeeds."""
        real = upgrade._write_file
        calls = []

        def failing(path, text):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("injected write failure")
            return real(path, text)
        upgrade._write_file = failing
        try:
            with self.assertRaises(OSError):
                uf.fresh_install(self.root, self.pv1, self.info)
        finally:
            upgrade._write_file = real
        self.assertFalse((self.root / ".claude" / "harness.json").exists())
        self.assertEqual([p for p in uf.tree(self.root)], [])                  # all or nothing: rolled back
        uf.fresh_install(self.root, self.pv1, self.info)
        self.assertTrue((self.root / ".claude" / "harness.json").exists())

    def test_hj08_unreadable_stamp_is_refused_and_untouched(self):
        """HJ-08: unreadable JSON -> error, file unchanged."""
        uf.write(self.root, ".claude/harness.json", "{not json")
        with self.assertRaises(stamp.StampError):
            upgrade.build_plan(self.root, upgrade.SF, self.info, uf.CORE, self.pv1, requested=["software"])
        self.assertEqual(uf.read(self.root, ".claude/harness.json"), "{not json")


if __name__ == "__main__":
    unittest.main()
