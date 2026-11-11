"""Phase 3, PR-01 to PR-05, GT-09, JR-04: proposal of an upgrade by the doctor, gates and journal."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta

import helpers
import upgrade_fixtures as uf
from hcore import doctor, gates, journal, upgrade

H = helpers.harness


def report(blocks_only: bool, changes: int = 3) -> dict:
    return {"upgrade": {"changes": changes, "blocks_only": blocks_only, "by_skill": {"software-factory": changes},
                        "classes": ["block"] if blocks_only else ["block", "convert"]}}


class GateCatalogTest(helpers.TempDirCase):
    def test_gt09_catalog_and_phase3_auto(self):
        """GT-09: the three gates are in the catalog; upgrade.convert is journaled
        with software-factory."""
        listed = {g["id"]: g for g in json.loads(H(["gate", "list"], self.tmp).stdout)["gates"]}
        for gid in ("upgrade.convert", "harness-doctor.upgrade-blocks", "harness-doctor.upgrade-other"):
            self.assertIn(gid, listed)
            self.assertEqual(listed[gid]["kind"], "b")
        self.assertIn("harness-doctor.upgrade", listed)      # AP-10, UA-02
        self.assertEqual(gates.get("upgrade.convert").skill, "software-factory")
        self.assertEqual(gates.get("upgrade.convert").default, "Keep as is")

    def test_pr01_pr02_pr03_proposal_by_policy(self):
        """PR-01, PR-02, PR-03, CA-24: blocks_only + auto -> default and launch; gate -> ask; other -> never launched,
        with the command and the blocking classes."""
        auto = doctor.proposal(report(True), "auto")
        self.assertEqual((auto["gate"], auto["decision"], auto["launch"]), ("harness-doctor.upgrade-blocks", "default", True))
        self.assertEqual(auto["event"]["default"], "Run the upgrade")
        asked = doctor.proposal(report(True), "gate")
        self.assertEqual((asked["decision"], asked["launch"]), ("ask", False))
        other = doctor.proposal(report(False), "auto")
        self.assertEqual(other["gate"], "harness-doctor.upgrade-other")
        self.assertFalse(other["launch"])
        self.assertEqual(other["blockers"], ["convert"])
        self.assertEqual(other["commands"], ["/software-factory --upgrade"])
        self.assertEqual(other["default"], "Do nothing")
        self.assertIsNone(doctor.proposal(report(True, 0), "auto"))

    def test_pr02_pr03_pr05_headless_fallback_and_journal(self):
        """PR-02, PR-03, PR-05: headless -> default for both gates; every decision is a `gate` event of the doctor's journal."""
        for gid, expected in (("harness-doctor.upgrade-blocks", "Run the upgrade"),
                              ("harness-doctor.upgrade-other", "Do nothing")):
            res = H(["gate", "decide", "--root", str(self.tmp), "--id", gid], self.tmp)
            self.assertEqual(res.returncode, 0, res.stderr)
            decided = json.loads(res.stdout)
            self.assertEqual(decided["headless"], "default")
            run = decided["run"]
            ans = H(["gate", "answer", "--root", str(self.tmp), "--run", run, "--id", gid, "--headless"], self.tmp)
            self.assertEqual(ans.returncode, 0, ans.stderr)
            self.assertEqual(json.loads(ans.stdout)["answer"], expected)
            self.assertIn("harness-doctor-", run)
            self.assertEqual(journal.validate_file(self.tmp / run), [])


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class ReplayTest(helpers.TempDirCase):
    def test_pr04_the_chain_ends_with_no_change(self):
        """PR-04: sf --upgrade, then ci --upgrade (each only if its plan is not empty), then the doctor shows 0 changes."""
        root, info, pv1, pv2 = uf.blocks_project(self.tmp)
        uf.write(root, ".claude/models.json", "{}\n")
        rep = doctor.report(root, info, uf.CORE, pv2, uf.ASSETS, None, None, uf.NOW)
        self.assertGreater(rep["upgrade"]["by_skill"]["software-factory"], 0)
        prop = doctor.proposal(rep, "auto")
        self.assertTrue(prop["launch"])
        for skill in ("software-factory", "code-index"):
            if rep["upgrade"]["by_skill"].get(skill):
                uf.plan_and_apply(root, skill, info, pv2, now=uf.NOW + timedelta(minutes=1))
        after = doctor.report(root, info, uf.CORE, pv2, uf.ASSETS, None, None, uf.NOW)
        self.assertEqual(after["upgrade"]["changes"], 0)

    def test_jr04_upgrade_and_doctor_journals_validate(self):
        """JR-04, CA-25: the journals of the replays (software-factory-*, harness-doctor-*) are valid; names follow JR-01."""
        root, info, pv1, pv2 = uf.blocks_project(self.tmp)
        res = uf.plan_and_apply(root, upgrade.SF, info, pv2, now=uf.NOW + timedelta(minutes=1))
        jpath = root / res["applied"]["journal"]
        self.assertRegex(jpath.name, journal.NAME_RE)
        self.assertEqual(H(["journal", "validate", str(jpath)], self.tmp).returncode, 0)
        self.assertEqual(journal.validate_record({"event": "doctor", "checks": {"gh": "warn"}}), [])
        self.assertTrue(journal.validate_record({"event": "doctor", "checks": {"gh": "bad"}}))
        self.assertTrue(journal.validate_record({"event": "upgrade", "file": "a", "kind": "nope", "from": None, "to": None}))
        self.assertIn("harness-doctor", journal.SKILLS)


if __name__ == "__main__":
    unittest.main()
