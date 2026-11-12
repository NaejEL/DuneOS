# Idempotent resume of the previous deliverables into AUDIT.md (MG-01 to MG-05).

import hashlib
import json
import shutil
import unittest

import helpers
import phase2
from hcore import unified

OLD = {"legacy-audit": "ROADMAP-legacy.md", "vibecode-audit": "AUDIT-vibecode.md", "ai-lab-audit": "AUDIT-lab.md"}
FIXTURE_NAMES = {"legacy-audit": "old-roadmap.md", "vibecode-audit": "old-vibecode.md",
                 "ai-lab-audit": "old-lab.md"}


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class MigrationTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.repo = phase2.project(self.tmp / "p", "profile-software")
        src = helpers.FIXTURES / "audit-legacy-deliverables"
        for skill, old in OLD.items():
            shutil.copyfile(str(src / FIXTURE_NAMES[skill]), str(self.repo / old))
        (self.repo / "specs").mkdir(exist_ok=True)
        shutil.copyfile(str(src / "specs" / "SPEC-leg-03-gitignore.md"),
                        str(self.repo / "specs" / "SPEC-leg-03-gitignore.md"))
        self.before = {name: (self.repo / name).read_bytes() for name in
                       list(OLD.values()) + ["specs/SPEC-leg-03-gitignore.md"]}

    def start(self, skill):
        src = helpers.write_output(self.repo, "%s-arguments.txt" % skill, "")
        res = helpers.harness(["audit", "start", "--unified", "--skill", skill, "--root", str(self.repo),
                               "--arguments-file", src], self.repo)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def import_all(self):
        blocks, imports = [], []
        for skill, old in OLD.items():
            out = self.start(skill)
            imports += out["imports"]
            text = (self.repo / old).read_text(encoding="utf-8")
            blocks.append((skill, unified.strip_set_block(skill, text)))
        return blocks, imports

    def assemble(self, blocks, imports):
        return unified.assemble(self.repo, blocks, "2026-10-31", ["software", "research"], ["all"], False, imports)

    def test_mg01_mg03_the_previous_deliverables_are_read_with_their_statuses_and_numbering(self):
        """MG-01, MG-03, CA-21: every previous deliverable not yet imported is read back: LEG-03 FAIT,
        VIB-02 EN_COURS, Q-01 TRANCHEE (journal 2026-09-12) are kept and the numbering goes on beyond the
        highest imported id."""
        legacy = self.start("legacy-audit")
        self.assertEqual([i["file"] for i in legacy["imports"]], ["ROADMAP-legacy.md"])
        self.assertEqual(legacy["imports"][0]["sha256"], hashlib.sha256(self.before["ROADMAP-legacy.md"]).hexdigest())
        self.assertEqual(legacy["resumed"]["numbering"], {"LEG": 3})
        self.assertIn("LEG-03", json.dumps(legacy["resumed"]))
        vibe = self.start("vibecode-audit")
        self.assertEqual(vibe["resumed"]["numbering"], {"VIB": 6})
        lab = self.start("ai-lab-audit")
        self.assertEqual(lab["resumed"]["numbering"]["Q"], 1)
        self.assertIn("EN_COURS", (self.repo / "AUDIT-vibecode.md").read_text(encoding="utf-8"))
        self.assertIn("TRANCHEE (journal 2026-09-12)", (self.repo / "AUDIT-lab.md").read_text(encoding="utf-8"))

    def test_mg02_mg05_imports_are_recorded_and_a_second_run_changes_nothing(self):
        """MG-02, MG-04, MG-05, CA-21: each import is recorded as `<!-- harness:import … sha256=… -->` plus a
        `Imported from` line; a second run (same date, same outputs) gives a byte-identical AUDIT.md with
        the same three import lines; the previous files and the existing spec stay identical."""
        blocks, imports = self.import_all()
        self.assertEqual(len(imports), 3)
        first = self.assemble(blocks, imports)
        text = (self.repo / "AUDIT.md").read_bytes()
        self.assertEqual(first["state"], "créé")
        body = text.decode("utf-8")
        self.assertEqual(body.count("<!-- harness:import "), 3)
        self.assertEqual(body.count("Imported from: "), 3)
        self.assertIn("Imported from: AUDIT-lab.md", body)
        # second run: nothing left to import, the blocks come back from AUDIT.md.
        again_blocks, again_imports = [], []
        for skill in OLD:
            out = self.start(skill)
            again_imports += out["imports"]
            again_blocks.append((skill, unified.slice_of(body, skill)))
        self.assertEqual(again_imports, [])
        second = self.assemble(again_blocks, again_imports)
        self.assertEqual(second["state"], "identique")
        self.assertEqual((self.repo / "AUDIT.md").read_bytes(), text)
        for name, content in self.before.items():
            self.assertEqual((self.repo / name).read_bytes(), content, name)

    def test_mg02_a_modified_previous_file_warns_and_is_not_reimported(self):
        """MG-02, CA-21: a previous file changed after its import -> warning, AUDIT.md wins, no new import."""
        blocks, imports = self.import_all()
        self.assemble(blocks, imports)
        (self.repo / "ROADMAP-legacy.md").write_text("# changed\n", encoding="utf-8")
        out = self.start("legacy-audit")
        self.assertEqual(out["imports"], [])
        self.assertTrue(any("ROADMAP-legacy.md changed" in w for w in out["import_warnings"]))
        self.assertEqual(out["resumed"]["numbering"], {"LEG": 3})

    def test_mg02_a_previous_file_never_imported_is_imported_even_when_audit_md_exists(self):
        """MG-02: AUDIT.md already exists (only the vibecode import recorded) and ROADMAP-legacy.md was never
        imported: it is imported now."""
        out = self.start("vibecode-audit")
        text = (self.repo / "AUDIT-vibecode.md").read_text(encoding="utf-8")
        unified.assemble(self.repo, [("vibecode-audit", unified.strip_set_block("vibecode-audit", text))],
                         "2026-10-31", ["software"], ["acces"], False, out["imports"])
        legacy = self.start("legacy-audit")
        self.assertEqual([i["file"] for i in legacy["imports"]], ["ROADMAP-legacy.md"])
        self.assertEqual(self.start("vibecode-audit")["imports"], [])

    def test_ad05_the_common_mapping_probes_git_the_factory_and_the_previous_deliverables(self):
        """AD-05, AU-12: the common mapping of a run reports GIT_ABSENT (no git repository here), whether a
        factory is installed, and the previous deliverables to import."""
        out = self.start("legacy-audit")
        self.assertTrue(out["git_absent"])
        self.assertFalse(out["factory"])
        self.assertEqual(out["allowed_writes"][0], "AUDIT.md")


if __name__ == "__main__":
    unittest.main()
