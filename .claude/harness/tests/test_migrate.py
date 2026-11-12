"""Phase 3, MI-01 to MI-07: migrations of an upgrade."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta

import helpers
import upgrade_fixtures as uf
from hcore import upgrade


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class MigrateTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        repo, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo)
        self.root = uf.make_legacy(self.tmp / "legacy")
        self.registry = ".harness/runs/ship-pr-20261001-101010.registry.jsonl"
        self.before = {rel: (self.root / rel).read_bytes() for rel in (self.registry, ".claude/lab-models.json")}

    def test_mi01_mi07_models_are_migrated_and_the_source_is_untouched(self):
        """MI-01, MI-07, CA-16: models.json created with lab.* keys; lab-models.json identical."""
        uf.plan_and_apply(self.root, upgrade.SF, self.info)
        models = json.loads(uf.read(self.root, ".claude/models.json"))
        self.assertTrue(models and all(k.startswith("lab.") for k in models), models)
        self.assertEqual((self.root / ".claude/lab-models.json").read_bytes(), self.before[".claude/lab-models.json"])

    def test_mi02_registries_are_published_once(self):
        """MI-02, MI-07, CA-16: REJECTED and FIXED published, CONFIRMED not; source unchanged; sha256 recorded; no republication."""
        uf.plan_and_apply(self.root, upgrade.SF, self.info)
        lines = [json.loads(l) for l in uf.read(self.root, ".claude/findings.jsonl").splitlines()]
        self.assertEqual(sorted(l["status"] for l in lines), ["FIXED", "REJECTED"])
        self.assertEqual((self.root / self.registry).read_bytes(), self.before[self.registry])
        stamp = json.loads(uf.read(self.root, ".claude/harness.json"))
        sources = stamp["migrations"][0]["sources"]
        self.assertEqual(sources[0]["path"], self.registry)
        import hashlib
        self.assertEqual(sources[0]["sha256"], hashlib.sha256(self.before[self.registry]).hexdigest())
        published = (self.root / ".claude/findings.jsonl").read_bytes()
        again = uf.plan_and_apply(self.root, upgrade.SF, self.info, now=uf.NOW + timedelta(minutes=1))
        self.assertEqual(again["changes"], [])
        self.assertEqual((self.root / ".claude/findings.jsonl").read_bytes(), published)

    def test_mi05_old_audit_deliverables_are_reported_not_migrated(self):
        """MI-05: ROADMAP-legacy.md, AUDIT-vibecode.md, AUDIT-lab.md stay as they are and are reported."""
        uf.write(self.root, "ROADMAP-legacy.md", "# old\n")
        plan = upgrade.build_plan(self.root, upgrade.SF, self.info, uf.CORE, None, now=uf.NOW, assets=uf.ASSETS)
        self.assertTrue(any("ROADMAP-legacy.md" in n and "repris par le prochain /audit" in n for n in plan["notes"]))
        self.assertNotIn("ROADMAP-legacy.md", [c["file"] for c in plan["changes"]])

    def test_mi06_missing_gitignore_lines_are_added_as_a_merge(self):
        """MI-06: `.harness/runs/` missing from a pre-core .gitignore -> added by union (class merge)."""
        plan = upgrade.build_plan(self.root, upgrade.SF, self.info, uf.CORE, None, now=uf.NOW, assets=uf.ASSETS)
        change = next(c for c in plan["changes"] if c["file"] == ".gitignore")
        self.assertEqual(change["kind"], "merge")
        self.assertIn(".harness/runs/", change["after"])
        self.assertIn("mine-2/", change["after"])

    def test_mi04_an_orchestrator_without_profile_blocks_goes_through_the_conversion(self):
        """MI-04: factory-run without harness.profile.* blocks -> conversion CV (not rewritten in place)."""
        plan = upgrade.build_plan(self.root, upgrade.SF, self.info, uf.CORE, None, now=uf.NOW, assets=uf.ASSETS)
        kinds = {c["file"]: c["kind"] for c in plan["changes"]}
        self.assertEqual(kinds[".claude/skills/factory-run/SKILL.md"], "convert")


if __name__ == "__main__":
    unittest.main()
