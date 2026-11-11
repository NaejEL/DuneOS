"""Phase 3, SN-01 to SN-06: /harness-doctor --scan."""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest
from datetime import timedelta

import helpers
import upgrade_fixtures as uf
from hcore import scan, upgrade


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class ScanTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo, self.shas = uf.make_repo(self.tmp, 3)
        self.pv1 = uf.make_profiles(self.tmp)
        self.base = self.tmp / "tree"
        self.install("current", self.shas[2])
        self.install("late", self.shas[0])
        uf.write(self.base, "inferred/.claude/skills/factory-run/SKILL.md", "x\n")            # no stamp
        uf.write(self.base, "plain/readme.txt", "x\n")                                         # not equipped
        uf.write(self.base, "node_modules/x/.claude/harness.json", json.dumps({"profiles": []}))
        uf.write(self.base, "a/b/c/d/deep/.claude/skills/factory-run/SKILL.md", "x\n")        # depth 5
        uf.write(self.base, "a/b/c/deep4/.claude/skills/factory-run/SKILL.md", "x\n")         # depth 4
        self.link_to("link", self.base / "current")
        self.touch_cycles()

    def install(self, name, commit):
        info = uf.repo_info(self.repo, commit=commit)
        uf.fresh_install(self.base / name, self.pv1, info)

    def link_to(self, name, target):
        link = self.base / name
        if os.name == "nt":
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            if made.returncode != 0:
                self.skipTest("cannot create a junction")
        else:
            os.symlink(str(target), str(link))

    def touch_cycles(self):
        uf.write(self.base, "current/.harness/runs/factory-run-20261201-120000.jsonl",
                 json.dumps({"agent": "factory-verifier", "verdict": "APPROVED"}) + "\n")
        newer = uf.write(self.base, "current/.harness/runs/ship-pr-20261130-090000.jsonl",
                         json.dumps({"agent": "reviewer", "verdict": "CHANGES_REQUESTED"}) + "\n")
        os.utime(str(newer), (time.time() + 500, time.time() + 500))            # modification date is a decoy
        uf.write(self.base, "current/.harness/runs/ship-pr-20261202-000000.registry.jsonl", "{}\n")

    def rows(self, *extra):
        res = helpers.subprocess.run([helpers.sys.executable, "-B", str(self.repo / "skills/_core/harness.py"),
                                      "doctor", "scan", "--root", str(self.base)] + list(extra),
                                     capture_output=True, text=True, encoding="utf-8")
        return res, [json.loads(l) for l in res.stdout.splitlines() if l.strip()]

    def test_sn01_sn02_sn03_sorted_lines_and_exclusions(self):
        """SN-01, SN-02, SN-03, CA-23: 3 lines at depth 3 (stamped current and late, one without stamp), sorted;
        node_modules, the link and the deep projects are not listed."""
        res, rows = self.rows()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual([r["path"] for r in rows], ["current", "inferred", "late"])
        by = {r["path"]: r for r in rows}
        self.assertEqual(by["current"]["lag"]["status"], "up_to_date")
        self.assertEqual((by["late"]["lag"]["status"], by["late"]["lag"]["commits"]), ("behind", 2))
        self.assertEqual(by["late"]["next"], "/software-factory --upgrade")
        self.assertEqual(by["inferred"]["equipped_by"], "inferred")
        self.assertEqual(set(by["late"]), {"path", "equipped_by", "skills", "lag", "last_cycle", "next"})

    def test_sn04_last_cycle_comes_from_the_name_of_the_journal(self):
        """SN-04: the most recent timestamp in the NAME wins over the modification date; verdict of the last line."""
        _, rows = self.rows()
        cycle = {r["path"]: r for r in rows}["current"]["last_cycle"]
        self.assertEqual(cycle["skill"], "factory-run")
        self.assertEqual(cycle["verdict"], "APPROVED")
        self.assertEqual(cycle["at"], "2026-12-01T12:00:00")

    def test_sn01_depth_option(self):
        """SN-01: --depth 4 lists the project at depth 4; --depth -1 -> exit 2."""
        _, rows = self.rows("--depth", "4")
        self.assertEqual(len(rows), 4)
        res, _ = self.rows("--depth", "-1")
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.rows("--depth", "x")[0].returncode, 2)
        missing = helpers.harness(["doctor", "scan", "--root", str(self.tmp / "nowhere")], self.tmp)
        self.assertEqual(missing.returncode, 2)

    def test_sn05_sn06_read_only_and_never_an_upgrade(self):
        """SN-05, SN-06: nothing is created or modified under the root (find -newer is empty)."""
        marker = self.tmp / "marker"
        marker.write_text("m", encoding="utf-8")
        past = time.time() + 5
        os.utime(str(marker), (past, past))
        before = {p: p.stat().st_mtime for p in self.base.rglob("*") if p.is_file()}
        self.rows()
        self.rows("--depth", "4")
        after = {p: p.stat().st_mtime for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse([p for p in self.base.rglob("*") if p.is_file() and p.stat().st_mtime > past - 1
                          and p not in before])

    def test_sn01_parse_depth(self):
        """SN-01: default 3; an integer >= 0 only."""
        self.assertEqual(scan.parse_depth(None), 3)
        self.assertEqual(scan.parse_depth("0"), 0)
        with self.assertRaises(scan.ScanError):
            scan.parse_depth("-1")


if __name__ == "__main__":
    unittest.main()
