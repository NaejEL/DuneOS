# Module hcore.findings and the 'finding' CLI: schema, validator, adapters,
# validator verdicts, conversions (FD-01 to FD-11, CA-12 to CA-15).

import copy
import json
import os
import shutil
import subprocess
import sys
import unittest

import helpers
from hcore import findings, ghflow, verdicts
from hcore.findings import FindingError

RGKB = helpers.CORE.parent / "code-index" / "assets" / "rgkb.py"

# One output of each of the 6 agent formats of FD-05, as written at BASE1.
REVIEWER = {"file": "src/sum.js", "line": 2, "side": "RIGHT", "severity": "major",
            "title": "reduce without initial value", "description": "crashes on []", "fix": "pass 0"}
LEGACY = {"id": "HYG-01", "category": "secrets-commites", "severity": "critical", "title": "Secret in config.py",
          "location": {"file": "config.py", "line": 12}, "evidence": "API_KEY = 'abc'",
          "impact": "key leaked", "remediation": "move it to the environment", "size": "S", "depends_on": []}
LAB_AUDIT = {"id": "VAL-01", "lens": "validite", "nature": "question", "severity": "major",
             "title": "Single seed", "location": {"file": None, "line": None, "journal_date": "2026-09-01"},
             "evidence": "seed 0 only", "impact": "effect may be noise",
             "proposal": "Does the effect hold over 5 seeds?", "size": "M", "depends_on": ["LEAK-01"]}
VIBECODE = {"id": "ACC-01", "axis": "securite", "pattern": "Ownership never checked server side",
            "severity": "critical",
            "occurrences": [{"file": "api/orders.js", "line": 10, "evidence": "Order.find(req.params.id)"},
                            {"file": "api/users.js", "line": 4, "evidence": "User.find(req.params.id)"}],
            "count": 7, "sampled": True, "impact": "read anyone's orders", "remediation": "check the owner",
            "difficulty": "M", "regression_risk": "moyen", "risk_rationale": "12 callers", "depends_on": []}
FACTORY_ISSUE = {"file": "src/sum.js", "severity": "major", "description": "empty array not handled\nsecond line"}
LAB_ISSUE = {"file": "", "severity": "critical", "kind": "leak", "description": "baseline seeded differently"}
CASES = (("shippr-reviewer", REVIEWER, "R1-F1"), ("legacy-auditor", LEGACY, None),
         ("lab-audit-auditor", LAB_AUDIT, None), ("vibecode-auditor", VIBECODE, None),
         ("factory-verifier", FACTORY_ISSUE, "V1-I1"), ("lab-verifier", LAB_ISSUE, "V1-I1"))


def good(**extra):
    f = {"id": "LEG-01", "source": "legacy-audit", "severity": "major", "axis": "hygiene",
         "title": "No .gitignore", "status": "PROPOSED",
         "evidence": {"kind": "structural", "file": None, "excerpt": "no .gitignore at the root"}}
    f.update(extra)
    return f


def check_ok(test, res):
    test.assertEqual(res.returncode, 0, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout)


class SchemaTest(helpers.TempDirCase):
    def write(self, name, data):
        path = self.tmp / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_fd01_fd03_ca12_conforming_findings_exit_0(self):
        """FD-01, FD-03, CA-12: conforming fixtures (one, a list, {findings}) -> exit 0."""
        full = good(id="VIB-01", source="vibecode-audit", axis="securite", status="CONFIRMED",
                    history=["PROPOSED"], category="acces", size="M", nature="correction", depends_on=["VIB-02"],
                    impact="i", remediation="r", regression_risk="faible", risk_rationale="tests", reason="ok",
                    evidence={"kind": "code", "file": "a.py", "lines": {"start": 3, "end": 5}, "excerpt": "x"},
                    occurrences=[{"file": "a.py", "line": 3, "excerpt": "x"}], count=4, sampled=True,
                    raw={"anything": [1]}, deliverable_status="TRANCHEE (journal 2026-09-01)")
        for data in (good(), [good(), full], {"findings": [full]}):
            res = check_ok(self, helpers.harness(["finding", "validate", self.write("f.json", data)], self.tmp))
            self.assertTrue(res["valid"])
            self.assertEqual(res["errors"], [])

    def test_fd02_fd03_ca12_invalid_findings_exit_1(self):
        """FD-02, FD-03, CA-12: invalid severity, status outside FD-02, occurrences
        not a list, evidence.kind=code without file, forbidden transition -> exit != 0
        with the list of the errors."""
        cases = {
            "severity": good(severity="blocker"),
            "status": good(status="APPROVED"),
            "occurrences": good(occurrences={"file": "a"}),
            "evidence.kind=code requires evidence.file": good(evidence={"kind": "code", "file": None}),
            "transition": good(status="FIXED", fix_commit="abc", history=["PROPOSED"]),
        }
        for needle, data in cases.items():
            res = helpers.harness(["finding", "validate", self.write("f.json", data)], self.tmp)
            self.assertEqual(res.returncode, 1, needle)
            out = json.loads(res.stdout)
            self.assertFalse(out["valid"])
            self.assertTrue(any(needle in e for e in out["errors"]), (needle, out["errors"]))

    def test_fd02_statuses_and_transitions(self):
        """FD-02: superset of D2 statuses, required companions, allowed transitions."""
        self.assertEqual(set(findings.STATUSES), {"PROPOSED", "CONFIRMED", "REJECTED", "DUPLICATE", "FIXED",
                                                  "SETTLED", "UNVALIDATED", "NOT_FIXED"})
        ok = [good(status="FIXED", fix_commit="abc", history=["PROPOSED", "CONFIRMED"]),
              good(status="SETTLED", settled_by="journal 2026-09-01", history=["PROPOSED"]),
              good(status="SETTLED", settled_by="user"),
              good(status="NOT_FIXED", reason="out of scope", history=["PROPOSED", "CONFIRMED"])]
        for f in ok:
            self.assertEqual(findings.validate(f)[0], [], f)
        bad = [good(status="FIXED", history=["PROPOSED", "CONFIRMED"]),
               good(status="SETTLED", settled_by="someone"),
               good(status="NOT_FIXED", history=["PROPOSED", "CONFIRMED"]),
               good(status="CONFIRMED", history=["PROPOSED", "REJECTED"]),
               good(deliverable_status="DONE")]
        for f in bad:
            self.assertTrue(findings.validate(f)[0], f)
        # FD-02, iteration 2: a confirmed finding is never rejected afterwards; a
        # finding left unfixed can be fixed later.
        self.assertTrue(findings.validate(good(status="REJECTED", history=["PROPOSED", "CONFIRMED"]))[0])
        self.assertEqual(findings.validate(good(status="FIXED", fix_commit="c", reason="r",
                                                history=["PROPOSED", "CONFIRMED", "NOT_FIXED"]))[0], [])
        with self.assertRaises(FindingError):
            findings.transition(good(status="CONFIRMED", history=["PROPOSED"]), "REJECTED")
        later = findings.transition(good(status="NOT_FIXED", reason="r", history=["PROPOSED", "CONFIRMED"]),
                                    "FIXED", fix_commit="c")
        self.assertEqual(later["history"], ["PROPOSED", "CONFIRMED", "NOT_FIXED"])
        moved = findings.transition(good(), "CONFIRMED", reason="r")
        self.assertEqual((moved["status"], moved["history"]), ("CONFIRMED", ["PROPOSED"]))
        with self.assertRaises(FindingError):
            findings.transition(moved, "PROPOSED")

    def test_fd04_ca12_four_line_excerpt_is_a_warning_not_a_rejection(self):
        """FD-04, CA-12: an excerpt of 4 lines -> exit 0 and a warning."""
        data = good(evidence={"kind": "code", "file": "a.py", "excerpt": "1\n2\n3\n4"})
        res = check_ok(self, helpers.harness(["finding", "validate", self.write("f.json", data)], self.tmp))
        self.assertTrue(res["valid"])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertIn("4 lines", res["warnings"][0])


class AdapterTest(helpers.TempDirCase):
    def test_fd05_ca13_six_formats_lossless(self):
        """FD-05, CA-13: each of the 6 formats converted passes CA-12 and raw equals the input."""
        for fmt, obj, fid in CASES:
            before = copy.deepcopy(obj)
            f = findings.adapt(fmt, obj, fid)
            self.assertEqual(findings.validate(f)[0], [], fmt)
            self.assertEqual(f["raw"], before, fmt)
            self.assertEqual(obj, before, fmt)
            self.assertEqual(f["status"], "PROPOSED")
        vib = findings.adapt("vibecode-auditor", VIBECODE)
        self.assertEqual((vib["title"], vib["count"], vib["size"], vib["category"]),
                         (VIBECODE["pattern"], 7, "M", "acces"))
        self.assertEqual(vib["occurrences"][1], {"file": "api/users.js", "line": 4, "excerpt": "User.find(req.params.id)"})
        lab = findings.adapt("lab-audit-auditor", LAB_AUDIT)
        self.assertEqual((lab["evidence"]["kind"], lab["evidence"]["journal_date"], lab["remediation"]),
                         ("journal", "2026-09-01", LAB_AUDIT["proposal"]))
        leg = findings.adapt("legacy-auditor", LEGACY)
        self.assertEqual((leg["axis"], leg["category"], leg["evidence"]["lines"]), ("hygiene", "secrets-commites",
                                                                                   {"start": 12, "end": 12}))

    def test_fd05_x7_verifier_conversion_is_partial_and_cli(self):
        """FD-05, X7: a Verifier issue has neither line nor excerpt; the CLI adapts a block."""
        f = findings.adapt("factory-verifier", FACTORY_ISSUE, "V1-I1")
        self.assertEqual((f["title"], f["evidence"]), ("empty array not handled", {"kind": "code", "file": "src/sum.js"}))
        self.assertEqual(findings.adapt("lab-verifier", LAB_ISSUE, "V1-I1")["evidence"]["kind"], "structural")
        with self.assertRaises(FindingError):
            findings.adapt("lab-verifier", dict(LAB_ISSUE, kind="vibes"), "V1-I1")
        with self.assertRaises(FindingError):
            findings.adapt("factory-verifier", FACTORY_ISSUE)
        with self.assertRaises(FindingError):
            findings.adapt("legacy-auditor", dict(LEGACY, severity="blocker"))
        block = self.tmp / "verifier.json"
        block.write_text(json.dumps({"verdict": "CHANGES_REQUESTED", "issues": [FACTORY_ISSUE, FACTORY_ISSUE]}),
                         encoding="utf-8")
        out = check_ok(self, helpers.harness(["finding", "adapt", "--format", "factory-verifier", "--input",
                                              str(block), "--ids", "V1-I1,V1-I2"], self.tmp))
        self.assertEqual([f["id"] for f in out["findings"]], ["V1-I1", "V1-I2"])
        bad = helpers.harness(["finding", "adapt", "--format", "factory-verifier", "--input", str(block)], self.tmp)
        self.assertEqual(bad.returncode, 2)


def verdict(**fields):
    return "Checked.\n" + json.dumps(dict({"verdict": "CONFIRMED", "reason": "real"}, **fields))


class VerdictTest(helpers.TempDirCase):
    def test_fd06_ca14_single_parser_fields_per_source(self):
        """FD-06, CA-14: SPLIT accepted for vibecode, refused (unusable) for ship-pr;
        nature_correction read for ai-lab-audit only (ignored elsewhere); no final
        block = dead agent."""
        split = verdict(verdict="SPLIT", split_into=[
            {"pattern": "a", "occurrences": ["api/orders.js:10"], "severity": "critical", "difficulty": "S"},
            {"pattern": "b", "occurrences": ["api/users.js:4"], "severity": "major", "difficulty": "M"}])
        self.assertEqual(findings.parse_verdict(split, "vibecode-audit")["verdict"], "SPLIT")
        with self.assertRaises(verdicts.UnusableOutput):
            findings.parse_verdict(split, "ship-pr")
        nature = verdict(nature_correction="question", size_correction="L")
        self.assertEqual(findings.parse_verdict(nature, "ai-lab-audit")["nature_correction"], "question")
        for source in ("ship-pr", "legacy-audit", "vibecode-audit"):
            self.assertNotIn("nature_correction", findings.parse_verdict(nature, source), source)
        self.assertEqual(findings.parse_verdict(verdict(size_correction="XS"), "legacy-audit")["size_correction"], "XS")
        self.assertIsNone(findings.parse_verdict(verdict(size_correction="null"), "legacy-audit")["size_correction"])
        for source in findings.VERDICT_FIELDS:
            with self.assertRaises(verdicts.UnusableOutput):
                findings.parse_verdict('{"verdict": "CONFIRMED", "reason": "x"} trailing text', source)
            with self.assertRaises(verdicts.UnusableOutput):
                findings.parse_verdict(None, source)
        # SP-04 goes through the same parser.
        with self.assertRaises(verdicts.UnusableOutput):
            verdicts.parse_validator(split)
        out = helpers.write_output(self.tmp, "validator.md", split)
        res = check_ok(self, helpers.harness(["finding", "verdict", "--source", "ship-pr", "--output", out], self.tmp))
        self.assertFalse(res["usable"])

    def test_fd06_ship_pr_validator_with_a_foreign_field_is_not_dead(self):
        """FD-06, iteration 2 (regression fixed): a ship-pr validator that adds a
        correction field of another source (size_correction, nature_correction,
        corrected_count...) keeps its verdict, the field is ignored as at HEAD."""
        extra = verdict(verdict="REJECTED", size_correction="XS", nature_correction="question",
                        corrected_count=3, axis_correction="securite")
        self.assertEqual(verdicts.parse_validator(extra), {"verdict": "REJECTED", "reason": "real"})
        self.assertEqual(findings.parse_verdict(extra, "ship-pr"), {"verdict": "REJECTED", "reason": "real"})
        self.assertEqual(findings.parse_verdict(extra, "legacy-audit"),
                         {"verdict": "REJECTED", "reason": "real", "size_correction": "XS"})
        out = helpers.write_output(self.tmp, "validator.md", extra)
        res = check_ok(self, helpers.harness(["finding", "verdict", "--source", "ship-pr", "--output", out], self.tmp))
        self.assertTrue(res["usable"])

    def test_fd07_false_occurrence_outside_the_sample_and_zero_count(self):
        """FD-07, iteration 2: a false occurrence absent from the sample still
        decrements count; corrected_count=0 (literal of the prompt template) means
        "not given"."""
        vib = findings.adapt("vibecode-auditor", VIBECODE)
        v = findings.parse_verdict(verdict(false_occurrences=["api/other.js:99", "api/users.js:4"],
                                           corrected_count=0), "vibecode-audit")
        self.assertIsNone(v["corrected_count"])
        [done] = findings.apply_verdict(vib, v)
        self.assertEqual(len(done["occurrences"]), 1)
        self.assertEqual((done["count"], done["sampled"]), (5, True))

    def test_fd07_apply_corrections_false_occurrences_and_split(self):
        """FD-07: corrections applied, false_occurrences removed and count
        recomputed, split into CONFIRMED parts (Q-F1)."""
        vib = findings.adapt("vibecode-auditor", VIBECODE)
        v = findings.parse_verdict(verdict(axis_correction="maintenabilite", severity_correction="major",
                                           difficulty_correction="L", false_occurrences=["api/users.js:4"]),
                                   "vibecode-audit")
        [done] = findings.apply_verdict(vib, v)
        self.assertEqual((done["status"], done["axis"], done["severity"], done["size"]),
                         ("CONFIRMED", "maintenabilite", "major", "L"))
        self.assertEqual([o["file"] for o in done["occurrences"]], ["api/orders.js"])
        self.assertEqual((done["count"], done["sampled"]), (6, True))
        v = findings.parse_verdict(verdict(corrected_count=1, false_occurrences=["api/users.js:4"]), "vibecode-audit")
        self.assertEqual(findings.apply_verdict(vib, v)[0]["count"], 1)
        split = findings.parse_verdict(verdict(verdict="SPLIT", split_into=[
            {"pattern": "a", "occurrences": ["api/orders.js:10"], "severity": "critical", "difficulty": "S"},
            {"pattern": "b", "occurrences": ["api/users.js:4", "x.js:9"], "severity": "major", "difficulty": "M"}]),
            "vibecode-audit")
        parts = findings.apply_verdict(vib, split)
        self.assertEqual([(p["id"], p["status"], p["count"], p["split_from"]) for p in parts],
                         [("ACC-01.1", "CONFIRMED", 1, "ACC-01"), ("ACC-01.2", "CONFIRMED", 2, "ACC-01")])
        self.assertEqual(parts[0]["occurrences"][0]["excerpt"], "Order.find(req.params.id)")
        for p in parts:
            self.assertEqual(findings.validate(p)[0], [])
        lab = findings.adapt("lab-audit-auditor", LAB_AUDIT)
        v = findings.parse_verdict(verdict(nature_correction="correction", size_correction="XS"), "ai-lab-audit")
        self.assertEqual(findings.apply_verdict(lab, v)[0]["nature"], "correction")
        rejected = findings.apply_verdict(lab, findings.parse_verdict(verdict(verdict="REJECTED"), "ai-lab-audit"))
        self.assertEqual(rejected[0]["status"], "REJECTED")
        with self.assertRaises(FindingError):
            findings.apply_verdict(rejected[0], v)


# BASE1 (031df12) ghflow.comment_body, frozen: "**[%s] %s**\n\n%s\n\nSuggested fix: %s".
FROZEN_COMMENTS = (
    (REVIEWER, "**[major] reduce without initial value**\n\ncrashes on []\n\nSuggested fix: pass 0"),
    (dict(REVIEWER, severity="minor", title="Naïve `date` $(x)", description="", fix=""),
     "**[minor] Naïve `date` $(x)**\n\n\n\nSuggested fix: "),
    (dict(REVIEWER, severity="critical", title="T", description=helpers.TRAP, fix="a\nb"),
     "**[critical] T**\n\n%s\n\nSuggested fix: a\nb" % helpers.TRAP),
)


class ConversionTest(helpers.TempDirCase):
    def test_fd10_ca15_pr_comment_identical_to_base1(self):
        """FD-10, CA-15: to_pr_comment equals BASE1 comment_body on 3 fixtures, and
        ghflow.comment_body now goes through it."""
        for cand, expected in FROZEN_COMMENTS:
            f = findings.adapt("shippr-reviewer", cand, "R1-F1")
            self.assertEqual(findings.to_pr_comment(f), expected)
            self.assertEqual(ghflow.comment_body(dict(cand, id="R1-F1", theme="logic")), expected)

    def sections(self, **extra):
        data = {"context": "aucun .gitignore.", "scope": "Créer un .gitignore.",
                "criteria": ["Un `.gitignore` existe.", "git status est propre."],
                "out_of_scope": ".gitignore par sous-dossier.", "risks": "Motif trop large."}
        data.update(extra)
        return data

    def test_fd08_ca15_spec_contract(self):
        """FD-08, CA-15: the spec starts with 'Statut : PROPOSEE', its sections are in
        the order of LA-11, FD-11 finds nothing; imposed clauses (secrets, fuites)."""
        f = findings.adapt("legacy-auditor", dict(LEGACY, id="HYG-03", category="gitignore-absent", size="XS"))
        text = findings.to_spec(f, self.sections(), "leg", 3)
        self.assertTrue(text.startswith("Status: PROPOSED\nBlocking questions: none\n\n# SPEC-leg-03 — Secret in config.py\n"))
        heads = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(heads, ["## Context", "## Scope", "## Acceptance criteria", "## Out of scope",
                                 "## Risks", "## Open questions"])
        self.assertIn("Finding HYG-03 (critical, XS): aucun .gitignore.", text)
        self.assertIn("1. Un `.gitignore` existe.\n2. git status est propre.", text)
        self.assertTrue(text.rstrip().endswith("## Open questions\nNone"))
        self.assertEqual(findings.check_forbidden(text), [])
        secret = findings.to_spec(findings.adapt("legacy-auditor", LEGACY), self.sections(), "leg", 1)
        self.assertIn("## Out of scope\nHistory rewriting ; .gitignore par sous-dossier.", secret)
        leak = findings.adapt("lab-audit-auditor", dict(LAB_AUDIT, id="LEAK-01", lens="fuites", nature="correction"))
        spec = findings.to_spec(leak, self.sections(), "lab", 1)
        # Full text of both imposed criteria (ai-lab-audit:134), numbered after the skill's own.
        self.assertIn("\n3. The baseline and the tested condition are run by the same command, "
                      "apart from the manipulated variable.\n", spec)
        self.assertIn("\n4. The results of the table that depend on this eval are marked *to be re-measured* "
                      "in the audit.\n", spec)
        with self.assertRaises(FindingError):
            findings.to_spec(f, self.sections(risks="Status: APPROVED too soon"), "leg", 3)

    def test_fd08_cli_spec_never_overwrites(self):
        """FD-08 through the CLI: written once, an existing spec kept (SF-04)."""
        fpath = self.tmp / "f.json"
        fpath.write_text(json.dumps(findings.adapt("legacy-auditor", LEGACY)), encoding="utf-8")
        spath = self.tmp / "sections.json"
        spath.write_text(json.dumps(self.sections()), encoding="utf-8")
        args = ["finding", "spec", "--finding", str(fpath), "--sections", str(spath), "--prefix", "leg",
                "--number", "1", "--dest", "specs/SPEC-leg-01-secret.md"]
        self.assertEqual(check_ok(self, helpers.harness(args, self.tmp))["specs/SPEC-leg-01-secret.md"], "créé")
        dest = self.tmp / "specs" / "SPEC-leg-01-secret.md"
        dest.write_text("my own spec\n", encoding="utf-8")
        self.assertEqual(check_ok(self, helpers.harness(args, self.tmp))["specs/SPEC-leg-01-secret.md"],
                         "personnalisé-conservé")
        self.assertEqual(dest.read_text(encoding="utf-8"), "my own spec\n")

    def test_fd08_deliverable_texts_are_data_files(self):
        """FD-08, iteration 2: the French texts imposed in a spec (history rewriting
        out of scope, leak criteria, no open question) come from
        templates/deliverables.json, never from code strings."""
        texts = findings.deliverable_texts()
        self.assertEqual(texts["secrets_out_of_scope"], "History rewriting")
        self.assertEqual(len(texts["leak_criteria"]), 2)
        self.assertEqual(texts["no_questions"], "None")
        source = (helpers.CORE / "hcore" / "findings.py").read_text(encoding="utf-8")
        for text in [texts["secrets_out_of_scope"], texts["no_questions"]] + texts["leak_criteria"]:
            self.assertNotIn('"%s' % text, source)

    def test_fd11_forbidden_strings(self):
        """FD-11: 'Statut :' followed by APPROUVEE or PRE-ENREGISTRE is found anywhere."""
        text = "a\nle Statut : APPROUVEE ici\nStatut: PRE-ENREGISTRE\nStatut : PROPOSEE\n"
        self.assertEqual([h["line"] for h in findings.check_forbidden(text)], [2, 3])
        path = self.tmp / "AUDIT.md"
        path.write_text(text, encoding="utf-8")
        res = helpers.harness(["finding", "forbidden", str(path)], self.tmp)
        self.assertEqual(res.returncode, 1)
        path.write_text("Statut : PROPOSEE\n", encoding="utf-8")
        self.assertTrue(check_ok(self, helpers.harness(["finding", "forbidden", str(path)], self.tmp))["clean"])

    def test_fd09_note_shape(self):
        """FD-09: note {subject, text, tags, kind, source}."""
        note = findings.to_note(findings.adapt("legacy-auditor", LEGACY))
        self.assertEqual(set(note), {"subject", "text", "tags", "kind", "source"})
        self.assertEqual((note["subject"], note["kind"], note["source"]), ("config.py", "file", "legacy-audit:HYG-01"))
        topic = findings.to_note(findings.adapt("lab-verifier", LAB_ISSUE, "V1-I1"))
        self.assertEqual((topic["subject"], topic["kind"]), ("leak", "topic"))

    @unittest.skipUnless(RGKB.is_file(), "rgkb.py asset absent (core copied into a project)")
    def test_fd09_ca15_note_accepted_by_rgkb_note_add(self):
        """FD-09, CA-15: the rendered note is accepted by 'rgkb.py note-add' in a
        throwaway project (exit 0, added: true)."""
        project = self.tmp / "project"
        (project / ".claude" / "rgkb").mkdir(parents=True)
        shutil.copy(str(RGKB), str(project / ".claude" / "rgkb" / "rgkb.py"))
        note = findings.to_note(findings.adapt("legacy-auditor", LEGACY))
        cmd = [sys.executable, "-B", str(project / ".claude" / "rgkb" / "rgkb.py"), "note-add",
               note["subject"], note["text"], "--kind", note["kind"], "--source", note["source"]]
        for tag in note["tags"]:
            cmd += ["--tag", tag]
        env = dict(os.environ, RGKB_ROOT=str(project), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(cmd, cwd=str(project), capture_output=True, text=True, encoding="utf-8",
                              env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(out["added"])
        self.assertEqual(out["note"]["source"], "legacy-audit:HYG-01")


if __name__ == "__main__":
    unittest.main()
