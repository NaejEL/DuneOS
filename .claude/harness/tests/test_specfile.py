# Spec files: header, questions, statuses, entry, settle (BQ-01 to BQ-11).
#
# BQ-10 (the Planner and the Director fragments impose the classification of the questions, a default
# for each non-blocking one and the file perimeter) is a property of the prose: not tested here.

import json
import unittest

import helpers
import phase4
from hcore import findings, journal, runs, specfile

BLOCKED = phase4.spec_text("BLOQUEE", header=["Questions bloquantes : Q2"], questions=[
    "- Q1 — Which format ?", "  Défaut : json", "- Q2 — Which database ?", "  Réponse :"])
ANSWERED = phase4.spec_text("BLOQUEE", header=["Questions bloquantes : Q2"], questions=[
    "- Q1 — Which format ?", "  Défaut : json", "- Q2 — Which database ?", "  Réponse : postgres"])


def out(test, res, code=0):
    test.assertEqual(res.returncode, code, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip() else None


class ParseTest(unittest.TestCase):
    def test_bq01_bq02_header_and_questions(self):
        """BQ-01, BQ-02: contiguous header after the status, questions with Défaut and Réponse."""
        text = phase4.spec_text("PROPOSEE", header=[
            "Questions bloquantes : Q2, Q3", "Périmètre fichiers : src/**, tests/**", "Dépend de : SPEC-leg-01, SPEC-b.md",
            "Priorité : 3"], questions=["- Q1 — a", "  Défaut : x", "- Q2 — b", "  Réponse : yes", "- Q3 — c"])
        p = specfile.parse(text)
        self.assertEqual(p["errors"], [])
        self.assertEqual((p["blocking"], p["unanswered"], p["perimeter"], p["depends_on"], p["priority"]),
                         (["Q2", "Q3"], ["Q3"], ["src/**", "tests/**"], ["SPEC-leg-01", "SPEC-b.md"], 3))
        self.assertEqual([q["default"] for q in p["questions"]], ["x", "", ""])

    def test_bq01_a_header_line_after_a_blank_line_is_not_header(self):
        """BQ-01: the header must follow the status line without a gap."""
        p = specfile.parse("Statut : PROPOSEE\n\nPriorité : 2\n")
        self.assertIsNone(p["priority"])

    def test_bq03_every_defect_is_an_error(self):
        """BQ-03: absent blocking id, bad priority, empty or absolute glob, no status line, unknown status."""
        cases = {
            "blocking id absent": phase4.spec_text(header=["Questions bloquantes : Q9"], questions=["- Q1 — a"]),
            "priority": phase4.spec_text(header=["Priorité : 0"]),
            "priority text": phase4.spec_text(header=["Priorité : high"]),
            "empty glob": phase4.spec_text(header=["Périmètre fichiers : src/**, ,tests"]),
            "absolute glob": phase4.spec_text(header=["Périmètre fichiers : /etc/**"]),
            "windows glob": phase4.spec_text(header=["Périmètre fichiers : C:\\x\\**"]),
            "no status": "# SPEC\n\nBody\n",
            "unknown status": "Statut : FINI\n",
        }
        for label, text in cases.items():
            self.assertTrue(specfile.parse(text)["errors"], label)

    def test_bq04_the_vocabulary_comes_from_the_profiles(self):
        """BQ-04: both families have their four roles."""
        known = specfile.vocabulary()
        roles = {}
        for word, (role, family, _line) in known.items():
            roles.setdefault(family, set()).add(role)
        self.assertEqual(len(roles), 2)
        for family, got in roles.items():
            self.assertEqual(got, {"proposed", "blocked", "ready", "delivered"}, family)
        self.assertEqual(specfile.role_of("BLOQUE"), "blocked")
        self.assertEqual(specfile.status_word("TERMINE — verdict"), "TERMINE")


class FileTest(helpers.TempDirCase):
    def spec(self, text, name="SPEC-a.md"):
        return self.tmp / phase4.write_spec(self.tmp, name, text)

    def test_bq08_set_status_changes_one_line_only(self):
        """BQ-08, CA-10: PROPOSEE -> APPROUVEE changes the status line and nothing else (CRLF kept)."""
        path = self.spec(phase4.spec_text("PROPOSEE", questions=["- Q1 — a"]))
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        before = path.read_bytes().split(b"\r\n")
        specfile.set_status(path, "APPROUVEE")
        after = path.read_bytes().split(b"\r\n")
        diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual((len(before), diff), (len(after), [0]))
        self.assertEqual(after[0], b"Statut : APPROUVEE")

    def test_bq08_the_admitted_transitions_only(self):
        """BQ-08: LIVREE -> PROPOSEE and any other transition are refused; the research family works alike."""
        path = self.spec(phase4.spec_text("LIVREE"))
        with self.assertRaises(specfile.SpecError):
            specfile.set_status(path, "PROPOSEE")
        path = self.spec(phase4.spec_text("PROPOSEE"))
        with self.assertRaises(specfile.SpecError):
            specfile.set_status(path, "LIVREE")
        with self.assertRaises(specfile.SpecError):
            specfile.set_status(path, "PRE-ENREGISTRE")                       # another family
        for first, second in (("BLOQUEE", "PROPOSEE"), ("BLOQUEE", "APPROUVEE")):
            path = self.spec(phase4.spec_text(first))
            specfile.set_status(path, second)
        path = self.spec(phase4.spec_text("PROPOSE"), "SPEC-r.md")
        specfile.set_status(path, "PRE-ENREGISTRE")
        specfile.set_status(path, "TERMINE — confirmed")
        self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], "Statut : TERMINE — confirmed")

    def test_bq03_check_cli_and_malformed_cases_exit_2(self):
        """BQ-03, CA-09: `spec check` renders the structure; a malformed spec exits 2."""
        path = self.spec(BLOCKED)
        d = out(self, helpers.harness(["spec", "check", str(path)], self.tmp))
        self.assertEqual((d["status"], d["blocking"], d["unanswered"]), ("BLOQUEE", ["Q2"], ["Q2"]))
        bad = self.spec(phase4.spec_text(header=["Priorité : -1"]), "SPEC-bad.md")
        self.assertEqual(helpers.harness(["spec", "check", str(bad)], self.tmp).returncode, 2)
        res = helpers.harness(["spec", "set-status", str(path), "LIVREE"], self.tmp)
        self.assertEqual(res.returncode, 2)

    def test_bq07_entry_modes_through_the_cli(self):
        """BQ-07, CA-09, CA-10: approve, build, plan, stop on open questions, stop on a delivered spec, exit 2."""
        cases = {"SPEC-p.md": phase4.spec_text("PROPOSEE"), "SPEC-a.md": phase4.spec_text("APPROUVEE"),
                 "SPEC-r.md": ANSWERED}
        modes = {}
        for name, text in cases.items():
            phase4.write_spec(self.tmp, name, text)
            src = helpers.write_output(self.tmp, "factory-run-arguments.txt", "specs/" + name)
            modes[name] = out(self, helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp))["mode"]
        self.assertEqual(modes, {"SPEC-p.md": "approve", "SPEC-a.md": "build", "SPEC-r.md": "approve"})
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", "add the export")
        self.assertEqual(out(self, helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp))["mode"],
                         "plan")
        phase4.write_spec(self.tmp, "SPEC-b.md", BLOCKED)
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", "specs/SPEC-b.md")
        res = helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp)
        self.assertEqual(res.returncode, 1, res.stdout)
        stop = sorted((self.tmp / ".harness" / "runs").glob("STOP-*.md"))[-1].read_text(encoding="utf-8")
        self.assertIn("# STOP — factory-run — blocked", stop)
        self.assertIn("Q2 — Which database ?", stop)
        run = sorted((self.tmp / ".harness" / "runs").glob("factory-run-*.jsonl"))[-1]
        self.assertIn("blocked", [x.get("event") for x in journal.read(run)])
        phase4.write_spec(self.tmp, "SPEC-d.md", phase4.spec_text("LIVREE"))
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", "specs/SPEC-d.md")
        res = helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp)
        self.assertEqual(res.returncode, 1)
        self.assertIn("already delivered", res.stderr)
        phase4.write_spec(self.tmp, "SPEC-x.md", "Statut : FINI\n")
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", "specs/SPEC-x.md")
        self.assertEqual(helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp).returncode, 2)

    def test_bq09_success_delivers_the_spec_whatever_the_policy(self):
        """BQ-09, CA-11: a successful cycle leaves `Statut : LIVREE` (policy gate, the default here)."""
        rel = phase4.write_spec(self.tmp, "SPEC-a.md", phase4.spec_text("APPROUVEE"))
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", rel)
        run = out(self, helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp))["run"]
        for agent, output in (("factory-builder", None), ("factory-verifier", helpers.verifier_block("APPROVED", True))):
            args = ["factory-run", "record", "--run", run, "--agent", agent, "--model", "inherit", "--duration-ms", "1"]
            if output:
                args += ["--output", helpers.write_output(self.tmp, "verifier-1.md", output)]
            d = out(self, helpers.harness(args, self.tmp))
        self.assertEqual(d["action"], "success")
        self.assertEqual((self.tmp / rel).read_text(encoding="utf-8").splitlines()[0], "Statut : LIVREE")

    def test_bq05_bq06_settle_under_auto(self):
        """BQ-05, BQ-06, CA-11: a blocking question stops (spec BLOQUEE, event, STOP); otherwise the defaults are applied."""
        phase4.stamp(self.tmp, autonomy="auto")
        text = phase4.spec_text("PROPOSEE", header=["Questions bloquantes : aucune"], questions=[
            "- Q1 — a ?", "  Défaut : one", "- Q2 — b ?", "  Défaut : two"])
        rel = phase4.write_spec(self.tmp, "SPEC-a.md", text)
        src = helpers.write_output(self.tmp, "factory-run-arguments.txt", "add the thing")
        run = out(self, helpers.harness(["factory-run", "start", "--arguments-file", src], self.tmp))["run"]
        d = out(self, helpers.harness(["spec", "settle", "--run", run, "--file", rel], self.tmp))
        self.assertEqual([a["id"] for a in d["applied"]], ["Q1", "Q2"])
        body = (self.tmp / rel).read_text(encoding="utf-8")
        self.assertEqual(body.count("(défaut appliqué automatiquement)"), 2)
        events = [x for x in journal.read(self.tmp / run) if x.get("event") == "gate" and x["id"] == "factory-run.questions"]
        self.assertEqual([e["phase"] for e in events], ["decision", "answer"])
        self.assertEqual(len(events[0]["applied"]), 2)
        self.assertEqual(journal.validate_file(self.tmp / run), [])
        # A blocking question without answer: BLOQUEE, `blocked` event, STOP, exit 1.
        rel2 = phase4.write_spec(self.tmp, "SPEC-b.md", BLOCKED.replace("BLOQUEE", "PROPOSEE", 1))
        run2 = out(self, helpers.harness(["factory-run", "start", "--arguments-file",
                                          helpers.write_output(self.tmp, "factory-run-arguments.txt", "other")], self.tmp))["run"]
        res = helpers.harness(["spec", "settle", "--run", run2, "--file", rel2], self.tmp)
        self.assertEqual(res.returncode, 1)
        self.assertEqual((self.tmp / rel2).read_text(encoding="utf-8").splitlines()[0], "Statut : BLOQUEE")
        self.assertIn("blocked", [x.get("event") for x in journal.read(self.tmp / run2)])

    def test_bq05_under_gate_nothing_changes(self):
        """BQ-05: under gate the questions are asked as before; the file is untouched."""
        rel = phase4.write_spec(self.tmp, "SPEC-a.md", BLOCKED.replace("BLOQUEE", "PROPOSEE", 1))
        before = (self.tmp / rel).read_bytes()
        run = out(self, helpers.harness(["factory-run", "start", "--arguments-file",
                                         helpers.write_output(self.tmp, "factory-run-arguments.txt", "x")], self.tmp))["run"]
        d = out(self, helpers.harness(["spec", "settle", "--run", run, "--file", rel], self.tmp))
        self.assertEqual(d["action"], "ask")
        self.assertEqual((self.tmp / rel).read_bytes(), before)

    def test_bq11_finding_specs_carry_the_header(self):
        """BQ-11: `Blocking questions: none`, `Depends on` and `Priority` in the spec of a finding."""
        sections = {"context": "c", "scope": "s", "criteria": ["one"], "out_of_scope": "o", "risks": "r",
                    "depends_on": ["SPEC-leg-01"], "priority": 2}
        finding = {"id": "HYG-03", "severity": "major", "title": "T", "size": "S", "axis": None, "category": "x"}
        text = findings.to_spec(finding, sections, "leg", 3)
        parsed = specfile.parse(text)
        self.assertEqual((parsed["blocking"], parsed["depends_on"], parsed["priority"], parsed["errors"]),
                         ([], ["SPEC-leg-01"], 2, []))
        self.assertTrue(text.startswith("Status: PROPOSED\nBlocking questions: none\nDepends on: SPEC-leg-01\n"))
        self.assertIn("\n## Open questions\n", text)


EN_SPEC = ("Status: APPROVED\nBlocking questions: Q2\nFiles scope: src/**, tests/**\nDepends on: SPEC-a, SPEC-b.md\n"
           "Priority: 3\n\n# SPEC\n\n## Open questions\n- Q1 — a\n  Default: x\n- Q2 — b\n  Answer: yes\n")
FR_SPEC = ("Statut : APPROUVEE\nQuestions bloquantes : Q2\nPérimètre fichiers : src/**, tests/**\n"
           "Dépend de : SPEC-a, SPEC-b.md\nPriorité : 3\n\n# SPEC\n\n## Questions ouvertes\n- Q1 — a\n  Défaut : x\n"
           "- Q2 — b\n  Réponse : yes\n")
PARSED_KEYS = ("word", "role", "blocking", "unanswered", "perimeter", "depends_on", "priority", "errors")


class LanguageTest(helpers.TempDirCase):
    def test_en_and_fr_specs_parse_alike(self):
        """English and French spellings give the same status, perimeter, priority, dependencies and questions."""
        en, fr = specfile.parse(EN_SPEC), specfile.parse(FR_SPEC)
        self.assertEqual(en["errors"], [])
        self.assertEqual(fr["errors"], [])
        self.assertEqual({k: en[k] for k in PARSED_KEYS}["perimeter"], ["src/**", "tests/**"])
        self.assertEqual(en["word"], "APPROVED")
        self.assertEqual(fr["word"], "APPROUVEE")
        for key in PARSED_KEYS:
            if key != "word":
                self.assertEqual(en[key], fr[key], key)
        self.assertEqual([(q["id"], q["default"], q["answer"]) for q in en["questions"]],
                         [(q["id"], q["default"], q["answer"]) for q in fr["questions"]])
        self.assertEqual((en["lang"], fr["lang"]), ("en", "fr"))

    def test_keywords_are_case_and_spacing_tolerant(self):
        text = "status :  proposed\nblocking   questions: NONE\nFILES SCOPE : src/**\ndepends on : SPEC-a\npriority: 2\n"
        p = specfile.parse(text)
        self.assertEqual((p["errors"], p["blocking"], p["perimeter"], p["depends_on"], p["priority"]),
                         ([], [], ["src/**"], ["SPEC-a"], 2))
        self.assertEqual(p["role"], "proposed")

    def test_a_transition_keeps_the_language_of_the_spec(self):
        en = self.tmp / "SPEC-en.md"
        fr = self.tmp / "SPEC-fr.md"
        en.write_text("Status: APPROVED\n\n# S\n", encoding="utf-8")
        fr.write_text("Statut : APPROUVEE\n\n# S\n", encoding="utf-8")
        self.assertEqual(specfile.set_status(en, "DELIVERED")["to"], "DELIVERED")
        self.assertEqual(en.read_text(encoding="utf-8").splitlines()[0], "Status: DELIVERED")
        self.assertEqual(specfile.set_status(fr, "LIVREE")["to"], "LIVREE")
        self.assertEqual(fr.read_text(encoding="utf-8").splitlines()[0], "Statut : LIVREE")
        # a word of the other language is written in the language of the spec, never mixed
        en.write_text("Status: APPROVED\n", encoding="utf-8")
        fr.write_text("Statut : APPROUVEE\n", encoding="utf-8")
        specfile.set_status(en, "LIVREE")
        specfile.set_status(fr, "DELIVERED")
        self.assertEqual(en.read_text(encoding="utf-8").splitlines()[0], "Status: DELIVERED")
        self.assertEqual(fr.read_text(encoding="utf-8").splitlines()[0], "Statut : LIVREE")
        # by role
        en.write_text("Status: PROPOSED\n", encoding="utf-8")
        fr.write_text("Statut : PROPOSEE\n", encoding="utf-8")
        specfile.set_role(en, "ready")
        specfile.set_role(fr, "blocked")
        self.assertEqual(en.read_text(encoding="utf-8").splitlines()[0], "Status: APPROVED")
        self.assertEqual(fr.read_text(encoding="utf-8").splitlines()[0], "Statut : BLOQUEE")

    def test_defaults_are_applied_in_the_language_of_the_spec(self):
        en = self.tmp / "SPEC-en.md"
        fr = self.tmp / "SPEC-fr.md"
        en.write_text("Status: PROPOSED\n\n## Open questions\n- Q1 — a\n  Default: x\n", encoding="utf-8")
        fr.write_text("Statut : PROPOSEE\n\n## Questions ouvertes\n- Q1 — a\n  Défaut : x\n", encoding="utf-8")
        self.assertEqual(specfile.apply_defaults(en)["applied"], [{"id": "Q1", "default": "x"}])
        specfile.apply_defaults(fr)
        self.assertIn("  Answer: x (default applied automatically)", en.read_text(encoding="utf-8"))
        self.assertIn("  Réponse : x (défaut appliqué automatiquement)", fr.read_text(encoding="utf-8"))
        self.assertEqual(specfile.parse(en.read_text(encoding="utf-8"))["questions"][0]["answer"],
                         "x (default applied automatically)")

    def test_what_the_core_writes_is_english(self):
        sections = {"context": "c", "scope": "s", "criteria": ["one"], "out_of_scope": "o", "risks": "r"}
        finding = {"id": "HYG-03", "severity": "major", "title": "T", "size": "S", "axis": None, "category": "x"}
        text = findings.to_spec(finding, sections, "leg", 3)
        self.assertTrue(text.startswith("Status: PROPOSED\nBlocking questions: none\n"))
        self.assertEqual(specfile.parse(text)["lang"], "en")
        self.assertEqual(specfile.parse(text)["errors"], [])


if __name__ == "__main__":
    unittest.main()
