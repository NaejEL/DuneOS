"""Phase 3, CV-01 to CV-05 (BACKLOG Q6): first upgrade of a generated file without harness block."""

from __future__ import annotations

import json
import unittest

import helpers
import upgrade_fixtures as uf
from hcore import blocks, convert, journal, upgrade

RG = "<!-- rgkb:debut -->\nR\n<!-- rgkb:fin -->\n"


class ClassifyTest(unittest.TestCase):
    def test_cv01_classes(self):
        """CV-01: pristine, suffix, modified (foreign blocks removed from file and base), untracked."""
        base = "Intro\nBody\n\n" + RG
        self.assertEqual(convert.classify(base, base)[0], convert.PRISTINE)
        self.assertEqual(convert.classify("Intro\nBody\n\n" + RG.replace("R", "other text"), base)[0], convert.PRISTINE)
        cls, rest = convert.classify(base + "\n## Mine\nx\n", base)
        self.assertEqual(cls, convert.SUFFIX)
        self.assertTrue(rest.startswith("<!-- rgkb:debut -->") and rest.endswith("## Mine\nx\n"))
        self.assertEqual(convert.classify(base.replace("Body", "Changed"), base)[0], convert.MODIFIED)
        self.assertEqual(convert.classify(base, None)[0], convert.UNTRACKED)


@unittest.skipUnless(uf.REPO_OK, uf.SKIP_REPO)
class LegacyConvertTest(helpers.TempDirCase):
    def setUp(self) -> None:
        super().setUp()
        repo, _ = uf.make_repo(self.tmp, 2)
        self.info = uf.repo_info(repo)
        self.root = uf.make_legacy(self.tmp / "legacy")
        self.builder = (self.root / ".claude/agents/factory-builder.md").read_bytes()
        self.lab_ps1 = (self.root / "ci/lab.ps1").read_bytes()

    def strip(self, rel):
        return blocks.strip_blocks(uf.read(self.root, rel))

    def test_cv02_cv03_cv05_pristine_and_suffix_are_converted_by_default(self):
        """CV-02, CV-03, CV-05, CA-18: converted with the foreign block and the user's suffix kept; never blocks_only."""
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info)
        kinds = {c["file"]: c["kind"] for c in res["changes"]}
        self.assertEqual(kinds[".claude/agents/factory-verifier.md"], "convert")          # pristine
        self.assertEqual(kinds[".claude/agents/factory-planner.md"], "convert")           # suffix
        self.assertEqual(kinds["ci/factory.sh"], "convert")                               # suffix
        self.assertFalse(res["blocks_only"])
        verifier = uf.read(self.root, ".claude/agents/factory-verifier.md")
        self.assertEqual(blocks.get_block(verifier, "rgkb", "md"), "Bloc rédigé à la main par le LLM.")
        self.assertIn("harness.body", verifier)
        self.assertIn("## Règles maison", self.strip(".claude/agents/factory-planner.md"))
        self.assertIn("export FACTORY_EXTRA=1", self.strip("ci/factory.sh"))
        self.assertEqual(self.strip("ci/factory.sh").count("#!/usr/bin/env bash"), 1)      # shebang stays line 1
        self.assertEqual(uf.read(self.root, "ci/factory.sh").split("\n")[0], "#!/usr/bin/env bash")

    def test_cv04_modified_and_untracked_are_kept_by_default_and_journaled(self):
        """CV-04, CA-18: gate upgrade.convert, default keep: identical files, kind legacy, two gate events."""
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info)
        self.assertEqual((self.root / ".claude/agents/factory-builder.md").read_bytes(), self.builder)
        self.assertEqual((self.root / "ci/lab.ps1").read_bytes(), self.lab_ps1)
        self.assertEqual(sorted(res["gate"]["files"]), [".claude/agents/factory-builder.md", "ci/lab.ps1"])
        files = json.loads(uf.read(self.root, ".claude/harness.json"))["skills"]["software-factory"]["files"]
        self.assertEqual(files[".claude/agents/factory-builder.md"]["kind"], "legacy")
        self.assertEqual(files["ci/lab.ps1"]["kind"], "legacy")
        events = [e for e in journal.read(self.root / res["applied"]["journal"]) if e.get("event") == "gate"]
        self.assertEqual([(e["id"], e["phase"]) for e in events],
                         [("upgrade.convert", "decision"), ("upgrade.convert", "answer")])
        self.assertEqual(events[1]["answer"], "Keep as is")

    def test_cv04_cv05_convert_flag_converts_with_a_backup(self):
        """CV-04, CV-05: --convert -> converted, the original is in the backup (cmp), logged as a user answer."""
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info, convert=True)
        self.assertIn("harness.body", uf.read(self.root, ".claude/agents/factory-builder.md"))
        self.assertIn("harness.ci", uf.read(self.root, "ci/lab.ps1"))
        backup = self.root / res["applied"]["backup"]
        self.assertEqual((backup / ".claude/agents/factory-builder.md").read_bytes(), self.builder)
        events = [e for e in journal.read(self.root / res["applied"]["journal"]) if e.get("event") == "gate"]
        self.assertEqual(events[-1]["answer"], "Convert (backup)")
        self.assertFalse(res["blocks_only"])

    def test_cv04_the_decision_file_converts_chosen_files_only(self):
        """CV-04: decisions at apply time: one file converted (backup), the other kept; the stamp follows."""
        decisions = {"files": {".claude/agents/factory-builder.md": "convert"}, "source": "user"}
        res = uf.plan_and_apply(self.root, upgrade.SF, self.info, decisions=decisions)
        self.assertIn("harness.body", uf.read(self.root, ".claude/agents/factory-builder.md"))
        self.assertEqual((self.root / "ci/lab.ps1").read_bytes(), self.lab_ps1)
        files = json.loads(uf.read(self.root, ".claude/harness.json"))["skills"]["software-factory"]["files"]
        self.assertEqual(files[".claude/agents/factory-builder.md"]["kind"], "generated")
        self.assertEqual(files["ci/lab.ps1"]["kind"], "legacy")
        self.assertEqual(res["applied"]["legacy"], ["ci/lab.ps1"])


if __name__ == "__main__":
    unittest.main()
