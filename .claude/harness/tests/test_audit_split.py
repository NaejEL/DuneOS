# Audit engine and core copy, iteration 3: vibecode resume after a SPLIT
# (vibecode-audit:82 on the parts and upstream of a split), explicit order
# with the resumed ids, resumed ids returned by the CLI, a column added by
# the user kept on rewrite, binary foreign files and the downgrade guard of
# 'sf copy-core --refresh'.

import json
import re
import shutil
import unittest
from pathlib import Path

import helpers
from hcore import audit, findings, fusion
from test_audit import AUDIT_FIXTURES, EngineCase, block, confirmed, legacy_item, replay, vib_item
from test_audit_resume import VIB

SPLIT_AGAIN = {"verdict": "SPLIT", "reason": "deux défauts", "severity_correction": None, "axis_correction": None,
               "difficulty_correction": None, "corrected_count": 0, "false_occurrences": [],
               "split_into": [{"pattern": "Les règles d'âge divergent entre client et serveur",
                               "occurrences": ["web/form.js:30", "api/users.js:12"], "severity": "major",
                               "difficulty": "S"},
                              {"pattern": "Un utilitaire de dates est réécrit dans deux écrans",
                               "occurrences": ["web/date.js:4", "web/x.js:7"], "severity": "minor",
                               "difficulty": "XS"}]}


def coarse(extra=()):
    places = (("web/form.js", 30), ("api/users.js", 12), ("web/date.js", 4)) + tuple(extra)
    return vib_item("MNT-01", "Les mêmes règles de validation sont écrites deux fois", places, "major",
                    "maintenabilite", "M")


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class SplitResumeReplayTest(helpers.TempDirCase):
    def rerun(self, first, maintenabilite, validators):
        fx = json.loads((AUDIT_FIXTURES / "vibecode-audit.json").read_text(encoding="utf-8"))
        fx["auditors"] = {"acces": [{"findings": []}], "injection": [None, None], "metier": [{"findings": []}],
                          "maintenabilite": [{"findings": maintenabilite}], "evolutivite": [{"findings": []}]}
        fx["validators"] = validators
        return replay(self, "vibecode-audit", self.tmp / "again", fx,
                      keep={"AUDIT-vibecode.md": first["deliverable"]})

    def test_vibecode_coarse_pattern_already_split_is_set_aside_upstream(self):
        """AU-06 (vibecode-audit:82, upstream of a split): the coarse pattern found
        again, whose occurrences are exactly the union of resumed parts of one axis,
        is set aside before any validator; nothing is renumbered."""
        first = replay(self, "vibecode-audit", self.tmp / "first")
        again = self.rerun(first, [coarse()], {})
        d = again["auditors"]["maintenabilite"][0]
        self.assertEqual([x["id"] for x in d["duplicates"]], ["MNT-01"])
        self.assertIn("VIB-04, VIB-05", d["duplicates"][0]["reason"])
        self.assertEqual(again["next"]["step"], "deliverable")
        self.assertEqual(again["deliverable"], first["deliverable"])

    def test_vibecode_split_again_parts_deduplicated_before_numbering(self):
        """AU-06, AU-08 (vibecode-audit:82 on the parts): the coarse pattern split
        again gives a part already in the report (same statement): it is set aside,
        never renumbered; the new part only gets the next VIB id."""
        first = replay(self, "vibecode-audit", self.tmp / "first")
        again = self.rerun(first, [coarse([("web/x.js", 7)])], {"MNT-01": [SPLIT_AGAIN]})
        split = again["validators"]["MNT-01"][0]
        self.assertEqual(split["action"], "split")
        self.assertEqual([x["id"] for x in split["duplicates"]], ["MNT-01.1"])
        self.assertIn("VIB-04", split["duplicates"][0]["reason"])
        self.assertEqual(again["consolidate"]["renamed"], {"MNT-01.2": "VIB-07"})
        self.assertEqual(again["deliverable"].count("Les règles d'âge divergent entre client et serveur |"), 1)
        self.assertIn("| VIB-07 | Un utilitaire de dates est réécrit dans deux écrans |", again["deliverable"])
        self.assertEqual(again["start"]["resumed"]["ids"]["attack"],
                         ["VIB-01", "VIB-02", "VIB-03", "VIB-04", "VIB-05", "VIB-06"])
        self.assertEqual(again["consolidate"]["resumed_ids"]["attack"][:2], ["VIB-01", "VIB-02"])


class SplitRunTest(EngineCase):
    def test_split_part_duplicate_of_the_run_and_union_of_run_parts(self):
        """AU-06: a split part duplicating a pattern of the run is set aside; a later
        pattern whose occurrences are the union of the parts of one split of the run
        is set aside before validation."""
        state, reg = self.start("vibecode-audit", "--axes maintenabilite;evolutivite")
        audit.record_auditor(state, reg, "evolutivite", block({"findings": [
            vib_item("EVO-01", "Les règles d'âge divergent entre client et serveur", (("z.js", 1),), "major",
                     "evolutivite")]}), False)
        audit.record_auditor(state, reg, "maintenabilite", block({"findings": [coarse([("web/x.js", 7)])]}), False)
        d, log = audit.record_validator(state, reg, "MNT-01", json.dumps(SPLIT_AGAIN), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["MNT-01.1"])
        self.assertEqual(state["findings"]["MNT-01.1"]["status"], findings.DUPLICATE)
        self.assertEqual(state["findings"]["MNT-01.2"]["status"], findings.CONFIRMED)
        self.assertEqual((log["validated"], log["rejected"]), (1, 1))
        state, reg = self.start("vibecode-audit", "--axes maintenabilite;evolutivite")
        audit.record_auditor(state, reg, "maintenabilite", block({"findings": [coarse([("web/x.js", 7)])]}), False)
        audit.record_validator(state, reg, "MNT-01", json.dumps(SPLIT_AGAIN), False)
        union = vib_item("EVO-03", "Un autre énoncé du même défaut", (("web/form.js", 30), ("api/users.js", 12),
                                                                    ("web/date.js", 4), ("web/x.js", 7)),
                         "major", "evolutivite")
        d, _ = audit.record_auditor(state, reg, "evolutivite", block({"findings": [union]}), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["EVO-03"])
        self.assertIn("split of MNT-01", d["duplicates"][0]["reason"])
        # The parts are included in this pattern, but their union is not exactly it: kept.
        partial = vib_item("EVO-04", "Encore un autre", (("web/form.js", 30), ("api/users.js", 12), ("web/date.js", 4),
                                                         ("web/x.js", 7), ("web/extra.js", 1)), "major", "evolutivite")
        state2, reg2 = self.start("vibecode-audit", "--axes maintenabilite;evolutivite")
        audit.record_auditor(state2, reg2, "maintenabilite", block({"findings": [coarse([("web/x.js", 7)])]}), False)
        audit.record_validator(state2, reg2, "MNT-01", json.dumps(SPLIT_AGAIN), False)
        d, _ = audit.record_auditor(state2, reg2, "evolutivite", block({"findings": [partial]}), False)
        self.assertEqual(d["duplicates"], [])


class OrderAndColumnsTest(EngineCase):
    DATA = {"name": "x", "date": "d", "axes": ["securite"], "path": "."}

    def test_explicit_order_must_list_the_resumed_attack_ids(self):
        """AU-08 (N10): an explicit order that leaves out a resumed pattern of the
        attack table is refused; listing it is accepted."""
        parsed = audit.parse_deliverable("vibecode-audit", VIB)
        state, reg = self.start("vibecode-audit", "--axes securite", resumed=parsed)
        audit.record_auditor(state, reg, "acces", block({"findings": [vib_item("ACC-01", "Nouveau motif",
                                                                                 (("n.js", 1),))]}), False)
        for name in ("injection", "metier"):
            audit.record_auditor(state, reg, name, block({"findings": []}), False)
        audit.record_validator(state, reg, "ACC-01", confirmed(), False)
        self.assertEqual(audit.resumed_ids(parsed)["attack"], ["VIB-01"])
        doc = audit.document(state, self.DATA)
        self.assertEqual([f["id"] for f in doc["findings"]], ["VIB-02"])
        for order in (["VIB-02"], ["VIB-02", "VIB-09"], ["VIB-01"]):
            with self.assertRaises(audit.AuditUsage, msg=order):
                audit.render("vibecode-audit", audit.document(state, dict(self.DATA, order=order)))
        text = audit.render("vibecode-audit", audit.document(state, dict(self.DATA, order=["VIB-02", "VIB-01"])))
        self.assertIn("| 1 | VIB-02 | Nouveau motif |", text)
        self.assertIn("| 2 | VIB-01 | Motif un |", text)

    def test_au09_column_added_by_the_user_is_kept_on_rewrite(self):
        """AU-09: a resumed table with a column added by the user keeps its header;
        a new row gets an empty cell there, the table stays aligned and reads back."""
        text = ("## Milestone 1 — Assainir\n| ID | Finding | Note | Severity | Size | Depends on | Spec | Status |\n"
                "|---|---|---|---|---|---|---|---|\n| LEG-01 | Secrets | à revoir | critical | S | — | — | FAIT |\n")
        parsed = audit.parse_deliverable("legacy-audit", text)
        f = dict(findings.adapt("legacy-auditor", legacy_item("HYG-02", "Gitignore", None, None, "gitignore",
                                                              "major", "XS")),
                 id="LEG-02", status="CONFIRMED", history=["PROPOSED"])
        doc = {"findings": [f], "rejected": [], "resumed": parsed,
               "data": {"name": "x", "date": "d", "factory": True, "milestones": [{"number": 1, "title": "Assainir"}],
                        "findings": {"LEG-02": {"milestone": 1}}}}
        out = audit.render("legacy-audit", doc)
        lines = out.splitlines()
        i = lines.index("## Milestone 1 — Assainir")
        self.assertEqual(lines[i + 1], "| ID | Finding | Note | Severity | Size | Depends on | Spec | Status |")
        self.assertEqual(lines[i + 3], "| LEG-01 | Secrets | à revoir | critical | S | — | — | FAIT |")
        self.assertEqual(lines[i + 4], "| LEG-02 | Gitignore |  | major | XS | — | — | A_FAIRE |")
        widths = {len(audit._split_cells(l)) for l in lines[i + 1:i + 5]}
        self.assertEqual(widths, {8})
        again = audit.parse_deliverable("legacy-audit", out)
        self.assertEqual([(e["id"], e["fields"]["status"]) for e in again["entries"]],
                         [("LEG-01", "FAIT"), ("LEG-02", "A_FAIRE")])


class CoreCopyGuardTest(helpers.TempDirCase):
    def h(self, *args):
        return helpers.harness(list(args), self.tmp)

    def test_uf05_binary_foreign_file_is_never_read(self):
        """UF-05 (check and refresh): a binary file that is not a core file is
        listed as foreign/extra, never decoded, and left byte for byte."""
        root = self.tmp / "p"
        fusion.copy_core(helpers.CORE, root)
        blob = root / ".claude" / "harness" / "logo.bin"
        blob.write_bytes(b"\xff\xfe\x00\x81binary")
        check = fusion.check_core_copy(helpers.CORE, root)
        self.assertEqual(check["extra"], ["logo.bin"])
        out = fusion.refresh_core(helpers.CORE, root)
        self.assertEqual(out["foreign"], [".claude/harness/logo.bin"])
        self.assertEqual(blob.read_bytes(), b"\xff\xfe\x00\x81binary")
        self.assertEqual(self.h("sf", "copy-core", "--root", str(root), "--check").returncode, 1)

    def test_uf05_refresh_refuses_a_downgrade_unless_forced(self):
        """UF-05 (sf copy-core --refresh): a copy declaring a higher CORE_VERSION
        than the source is left as it is (exit 1) unless --force; --force without
        --refresh is a usage error."""
        root = self.tmp / "p"
        fusion.copy_core(helpers.CORE, root)
        init = root / ".claude" / "harness" / "hcore" / "__init__.py"
        newer = re.sub(r'CORE_VERSION = "[^"]+"', 'CORE_VERSION = "99.0.0"', init.read_text(encoding="utf-8"))
        init.write_text(newer, encoding="utf-8")
        res = self.h("sf", "copy-core", "--root", str(root), "--refresh")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("downgrade", res.stderr + res.stdout)
        self.assertEqual(init.read_text(encoding="utf-8"), newer)
        self.assertEqual(self.h("sf", "copy-core", "--root", str(root), "--force").returncode, 2)
        res = self.h("sf", "copy-core", "--root", str(root), "--refresh", "--force")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(fusion.check_core_copy(helpers.CORE, root)["identical"])
        self.assertLess(fusion._version_key("1.0.0"), fusion._version_key("1.0.10"))
        # A copy checked out with CRLF line endings is still a recognized copy.
        init.write_bytes(init.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.assertTrue(fusion.is_core_copy(root / ".claude" / "harness"))


if __name__ == "__main__":
    unittest.main()
