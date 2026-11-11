# Audit engine, iteration 2: robust reading of an existing deliverable
# (AU-09), resumed statuses, vibecode-audit resume (AU-06, AU-08), merge
# independence (VA-07 hook), headless minors, models read without writing
# (X5), and the bounds left uncovered by the first mutation campaign.

import json
import unittest
from pathlib import Path

import helpers
from hcore import audit, findings, gates, registry
from test_audit import (AUDIT_FIXTURES, EngineCase, block, check_ok, confirmed, legacy_item, replay,
                        vib_item)

HEAD = "| ID | Finding | Severity | Size | Depends on | Spec | Status |\n|---|---|---|---|---|---|---|\n"
VIB = ("## Where to attack\nOrder retained: x.\n\n"
       "| # | ID | Pattern | Axis | Sev. | Occ. | Diff. | Reg. risk | Validated | Depends on | Status |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|\n"
       "| 1 | VIB-01 | Motif un | security | critical | 3 | M | high | not validated | — | A_FAIRE |\n\n"
       "## Patterns in detail\n### VIB-01 — Motif un\n- **Occurrences**: `a.js:1`, `b.js:2`\n- **Status**: A_FAIRE\n")


class ParseTest(EngineCase):
    def test_au09_unescaped_pipe_and_extra_column_are_read_by_the_header(self):
        """AU-09: columns are found by the header; an extra column is tolerated, an
        unescaped '|' in a title stays in the title; status and id are read."""
        text = ("## Milestone 1 — A\n| ID | Finding | Note | Severity | Size | Depends on | Spec | Status |\n"
                "|---|---|---|---|---|---|---|---|\n"
                "| LEG-01 | Gitignore | bis | perso | major | XS | — | — | FAIT |\n")
        e = audit.parse_deliverable("legacy-audit", text)["entries"][0]
        self.assertEqual((e["id"], e["title"], e["status"]), ("LEG-01", "Gitignore | bis", "FAIT"))
        self.assertEqual(e["fields"]["severity"], "major")
        moved = audit.set_status("legacy-audit", e, "REPORTE")
        self.assertTrue(moved["line"].endswith("| REPORTE |"))

    def test_au09_unreadable_line_is_an_explicit_stop_naming_it(self):
        """AU-09: a finding line that cannot be read stops (exit 1) with its line
        number, never a silent loss; also a finding line outside the known
        sections and a table header without the expected columns."""
        for text, number in (("## Milestone 1 — A\n" + HEAD + "| LEG-01 | trop court |\n", 4),
                             ("## Milestone 1 — A\n" + HEAD + "| pas un id | a | major | S | — | — | FAIT |\n", 4),
                             ("## Other section\n| LEG-09 | perdu |\n", 2),
                             ("## Milestone 1 — A\n| ID | Finding |\n|---|---|\n| LEG-01 | a |\n", 2)):
            with self.assertRaises(audit.AuditStop, msg=text) as ctx:
                audit.parse_deliverable("legacy-audit", text)
            self.assertIn("line %d" % number, str(ctx.exception))
        with self.assertRaises(audit.AuditStop):
            audit.parse_deliverable("ai-lab-audit", "## Research questions retained (recommended order)\n### Q-01 sans tiret\n")
        repo = self.tmp / "repo"
        repo.mkdir()
        (repo / "ROADMAP-legacy.md").write_text("## Milestone 1 — A\n" + HEAD + "| LEG-01 | x |\n", encoding="utf-8")
        args = helpers.write_output(repo, "a.txt", "")
        res = helpers.harness(["audit", "start", "--skill", "legacy-audit", "--arguments-file", args], repo)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("ROADMAP-legacy.md, line 4", res.stderr)
        self.assertFalse(any(p.suffix == ".jsonl" for p in (repo / ".harness" / "runs").iterdir()))

    def test_au08_au09_numbering_from_every_id_of_the_text(self):
        """AU-08, AU-09: the numbering goes on from the highest id found anywhere in
        the text (a dependency cell included), not from the parsed lines only."""
        text = "## Milestone 1 — A\n" + HEAD + "| LEG-02 | a | major | S | LEG-07 | — | FAIT |\n"
        self.assertEqual(audit.parse_deliverable("legacy-audit", text)["numbering"], {"LEG": 7})

    def test_au09_resumed_ids_counted_once_and_unvalidated_kept(self):
        """AU-09: a row and a detail block of the same id are one finding (one
        registry event); a resumed 'not validated' stays UNVALIDATED (never CONFIRMED,
        never published)."""
        parsed = audit.parse_deliverable("vibecode-audit", VIB)
        self.assertEqual(len(parsed["entries"]), 2)
        reg = registry.Registry(self.tmp / ".harness" / "runs" / "v.registry.jsonl")
        self.assertEqual(audit.register_resumed(parsed, reg), 1)
        self.assertEqual(len(reg.path.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(reg.current("::motif un"), findings.UNVALIDATED)
        state, _ = self.start("vibecode-audit")
        state["git_absent"] = True
        out = audit.publish(state, reg, "vibecode-audit-20261017-000000.jsonl", True)
        self.assertEqual(out["published"], 0)

    def test_au06_duplicate_never_overwrites_a_resumed_final_state(self):
        """AU-06: a finding set aside as a duplicate of a resumed one leaves the run
        registry state of that key unchanged."""
        text = ("## Findings rejected at validation\n| ID | Finding | Reason |\n|---|---|---|\n"
                "| HYG-07 | Gros fichier | fixture |\n")
        parsed = audit.parse_deliverable("legacy-audit", text)
        state, reg = self.start("legacy-audit", "--categories hygiene", resumed=parsed)
        audit.register_resumed(parsed, reg)
        item = legacy_item("HYG-01", "Gros fichier", None, None, "fichiers-geants")
        d, _ = audit.record_auditor(state, reg, "hygiene", block({"findings": [item]}), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["HYG-01"])
        self.assertEqual(reg.current("::gros fichier"), findings.REJECTED)

    def test_au06_lab_resumed_question_blocks_dedup(self):
        """AU-06: ai-lab-audit questions are blocks, not table rows: a new finding of
        the same title as a resumed question is set aside."""
        text = ("## Research questions retained (recommended order)\n### Q-02 — Seeds insuffisants\n"
                "- **Question**: q ?\n- **Status**: A_FAIRE\n")
        parsed = audit.parse_deliverable("ai-lab-audit", text)
        state, reg = self.start("ai-lab-audit", "--lenses validite", resumed=parsed)
        item = {"id": "VAL-01", "lens": "validite", "nature": "question", "severity": "major",
                "title": "Seeds insuffisants", "location": {"file": None, "line": None, "journal_date": None},
                "evidence": "e", "impact": "i", "proposal": "p", "size": "S", "depends_on": []}
        d, _ = audit.record_auditor(state, reg, "validite", block({"findings": [item]}), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["VAL-01"])
        self.assertIn("Q-02", d["duplicates"][0]["reason"])


class BoundsTest(EngineCase):
    def test_qj_option_given_twice_is_exit_2(self):
        """Q-J (AU-02, AU-03): an option given twice is a usage error."""
        for skill, arg in (("legacy-audit", "--max-specs 3 --max-specs 4"),
                           ("vibecode-audit", "--axes securite --axes evolutivite")):
            with self.assertRaises(audit.AuditUsage):
                audit.parse_arguments(skill, arg, self.tmp)

    def test_au10_tranchee_needs_a_real_date(self):
        """AU-10: TRANCHEE (journal <date>) with an impossible or missing date is refused."""
        lab = audit.profile("ai-lab-audit")
        for bad in ("TRANCHEE (journal 2026-13-45)", "TRANCHEE (journal 2026-02-30)", "TRANCHEE (journal hier)",
                    "TRANCHEE (journal )", "TRANCHEE"):
            with self.assertRaises(audit.AuditUsage, msg=bad):
                audit.check_status(lab, bad)
        audit.check_status(lab, "TRANCHEE (journal 2024-02-29)")

    def test_au11_claude_folder_is_not_writable_but_runs_and_registry(self):
        """AU-11 (X5): only .harness/runs/ and the project registry under .claude/."""
        for rel in (".claude/settings.json", ".claude/models.json", ".claude/harness/harness.py",
                    ".claude/findings.jsonl.bak", "specs/other.md"):
            with self.assertRaises(audit.AuditUsage, msg=rel):
                audit.check_writable("legacy-audit", self.tmp, self.tmp / rel)
        for rel in (".harness/runs/x.md", ".claude/findings.jsonl", ".claude/findings.lock"):
            audit.check_writable("legacy-audit", self.tmp, self.tmp / rel)

    def test_au14_spec_without_slug_is_kept(self):
        """AU-14: an existing specs/SPEC-leg-<nn>.md (no slug) is kept as it is."""
        f = findings.adapt("legacy-auditor", legacy_item(size="XS"))
        sections = {"context": "c", "scope": "s", "criteria": ["c1"], "out_of_scope": "o", "risks": "r"}
        (self.tmp / "specs").mkdir()
        plain = self.tmp / "specs" / "SPEC-leg-05.md"
        plain.write_text("mine\n", encoding="utf-8")
        out = audit.write_spec("legacy-audit", self.tmp, f, sections, 5, "x")
        self.assertEqual(out, {"file": "specs/SPEC-leg-05.md", "state": "personnalisé-conservé"})
        self.assertEqual(plain.read_text(encoding="utf-8"), "mine\n")
        self.assertFalse((self.tmp / "specs" / "SPEC-leg-05-x.md").exists())

    def test_au04_surface_is_major_in_the_state_and_the_report(self):
        """AU-04: the unauditable surface of a dead family is recorded major and
        rendered in the surfaces table."""
        state, reg = self.start("vibecode-audit", "--axes securite")
        for name in ("acces", "injection", "metier"):
            audit.record_auditor(state, reg, name, None, True)
            audit.record_auditor(state, reg, name, None, True)
        self.assertEqual([s["severity"] for s in state["surfaces"]], ["major"] * 3)
        data = {"name": "x", "date": "d", "axes": ["securite"], "path": "."}
        text = audit.render("vibecode-audit", audit.document(state, data))
        rows = [l for l in text.splitlines() if l.startswith("| family ")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("major" in r for r in rows))

    def test_au15_readjust_label_comes_from_the_catalog(self):
        """AU-15: the readjustment option is the label of the GT-08 catalog."""
        state, _ = self.start("legacy-audit")
        gate = gates.CATALOG["legacy-audit.final"]
        renamed = gate._replace(options=(gate.options[0], "Revoir", gate.options[2]))
        gates.CATALOG["legacy-audit.final"] = renamed
        try:
            self.assertEqual(audit.readjust_option("legacy-audit"), "Revoir")
            audit.readjust(state)
            self.assertEqual(audit.final_options(state), [gate.options[0], gate.options[2]])
        finally:
            gates.CATALOG["legacy-audit.final"] = gate
        for skill in audit.PROFILES:
            self.assertIn(audit.readjust_option(skill), gates.get(audit.profile(skill).final_gate).options)


class MergeTest(EngineCase):
    def test_vibecode_merge_is_independent_of_the_call_order_and_the_twin_status(self):
        """AU-06 / VA-07 hook: the same pattern from two auditors gives a merge,
        whatever the call order and even when the twin is already validated."""
        for first, second in (("acces", "metier"), ("metier", "acces")):
            state, reg = self.start("vibecode-audit")
            prefix = {"acces": "ACC", "metier": "MET"}
            audit.record_auditor(state, reg, first, block({"findings": [vib_item(prefix[first] + "-01", "P")]}), False)
            d, _ = audit.record_auditor(state, reg, second, block({"findings": [vib_item(prefix[second] + "-01", "P")]}),
                                        False)
            self.assertEqual(d["duplicates"], [])
            self.assertEqual(d["merge"], [{"finding": prefix[second] + "-01", "with": prefix[first] + "-01"}])
        state, reg = self.start("vibecode-audit")
        audit.record_auditor(state, reg, "acces", block({"findings": [vib_item("ACC-01", "P")]}), False)
        audit.record_validator(state, reg, "ACC-01", confirmed(), False)
        d, _ = audit.record_auditor(state, reg, "metier", block({"findings": [vib_item("MET-01", "P")]}), False)
        self.assertEqual((d["duplicates"], d["merge"]), ([], [{"finding": "MET-01", "with": "ACC-01"}]))


class HeadlessMinorsTest(EngineCase):
    def test_headless_minors_follow_the_default_of_the_order_gate(self):
        """VA-11 hook: in a headless run, the default of vibecode-audit.order details
        the minor patterns: a minor listed short is refused; an unvalidated
        pattern is never listed short (it would lose its 'not validated' mark)."""
        state, reg = self.start("vibecode-audit", "--axes maintenabilite")
        audit.record_auditor(state, reg, "maintenabilite", block({"findings": [
            vib_item("MNT-01", "p1", (("a.js", 1),), "minor", "maintenabilite"),
            vib_item("MNT-02", "p2", (("b.js", 1),), "minor", "maintenabilite")]}), False)
        audit.record_validator(state, reg, "MNT-01", confirmed(), False)
        audit.record_validator(state, reg, "MNT-02", None, True)
        audit.record_validator(state, reg, "MNT-02", None, True)
        base = {"name": "x", "date": "d", "axes": ["maintenabilite"], "path": "."}
        doc = audit.document(state, dict(base, headless=True, findings={"VIB-01": {"detail": False, "reason": "minor"}}))
        order = {f["id"]: f for f in doc["findings"]}
        minor = [i for i, f in order.items() if f["status"] == "CONFIRMED"][0]
        unvalidated = [i for i, f in order.items() if f["status"] == "UNVALIDATED"][0]
        with self.assertRaises(audit.AuditUsage):
            audit.render("vibecode-audit", audit.document(state, dict(base, headless=True, findings={
                minor: {"detail": False, "reason": "minor"}})))
        audit.render("vibecode-audit", audit.document(state, dict(base, findings={
            minor: {"detail": False, "reason": "minor"}})))
        with self.assertRaises(audit.AuditUsage):
            audit.render("vibecode-audit", audit.document(state, dict(base, findings={
                unvalidated: {"detail": False, "reason": "cap"}})))
        fx = json.loads((AUDIT_FIXTURES / "vibecode-audit.json").read_text(encoding="utf-8")) \
            if helpers.HAS_FIXTURES else None
        if fx:
            self.assertTrue(fx["data"]["headless"])
            self.assertFalse(any(not e.get("detail", True) for e in fx["data"]["findings"].values()))


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class ResumeReplayTest(helpers.TempDirCase):
    def again(self, skill, first, auditors=None, validators=None, data=None):
        fx = json.loads((AUDIT_FIXTURES / ("%s.json" % skill)).read_text(encoding="utf-8"))
        dead = {a: o for a, o in fx["auditors"].items() if all(x is None or isinstance(x, str) for x in o)}
        fx["auditors"] = auditors if auditors is not None else dict(
            {a: [{"findings": []}] for a in fx["auditors"]}, **dead)
        fx["validators"] = validators or {}
        fx["specs"] = []
        if data:
            fx["data"] = dict(fx["data"], **data)
        keep = {audit.profile(skill).deliverable: first["deliverable"]}
        return replay(self, skill, self.tmp / ("again-" + skill), fx, keep=keep)

    def test_au09_rerun_on_the_produced_deliverable_is_identical(self):
        """AU-09: running again on the produced deliverable, with nothing new,
        gives an identical file, for the three audits."""
        for skill in ("legacy-audit", "ai-lab-audit", "vibecode-audit"):
            first = replay(self, skill, self.tmp / skill)
            second = self.again(skill, first)
            self.assertEqual(second["deliverable"], first["deliverable"], skill)

    def test_au06_au08_vibecode_resume_dedup_order_and_box(self):
        """AU-06 (vibecode-audit:82, resumed report included: same statement or same
        occurrences from the detail blocks), AU-08 (a new critical takes its place
        before the resumed majors, numbering goes on), validation box, quick wins
        and raised criticals counting the resumed lines."""
        first = replay(self, "vibecode-audit", self.tmp / "first")
        same_places = vib_item("MET-05", "Reformulation du montant client", (("api/checkout.js", 21),
                                                                          ("api/refund.js", 8)), "major")
        same_text = vib_item("ACC-05", "Le schéma de données est modifié sans historique de migration",
                             (("db/other.sql", 3),), "critical", "evolutivite", "L")
        new_crit = vib_item("ACC-06", "Une route d'administration est ouverte sans authentification",
                            (("api/admin.js", 1),), "critical", "securite", "S")
        auditors = {"acces": [{"findings": [same_text, new_crit]}], "injection": [None, None],
                    "metier": [{"findings": [same_places]}], "maintenabilite": [{"findings": []}],
                    "evolutivite": [{"findings": []}]}
        second = self.again("vibecode-audit", first, auditors, {"ACC-06": [json.loads(confirmed())]})
        dismissed = sorted(d["id"] for ds in second["auditors"].values() for x in ds for d in x.get("duplicates", []))
        self.assertEqual(dismissed, ["ACC-05", "MET-05"])
        self.assertEqual(second["consolidate"]["renamed"], {"ACC-06": "VIB-07"})
        rows = [l for l in second["deliverable"].splitlines() if l.startswith("| ") and "VIB-" in l and "| # |" not in l]
        ids = [l.split(" | ")[1] for l in rows if l.split(" | ")[0].strip("| ").isdigit()]
        # Critical first, then axis, then difficulty: the new S critical leads.
        self.assertEqual(ids[:4], ["VIB-07", "VIB-01", "VIB-02", "VIB-03"])
        self.assertIn("| 1 | VIB-07 |", second["deliverable"])
        self.assertIn("| 2 | VIB-01 |", second["deliverable"])
        self.assertIn("**Validation.** 6 patterns out of 7", second["deliverable"])
        self.assertIn(": VIB-07, VIB-03, VIB-04. Flagged", second["deliverable"])
        self.assertIn(": VIB-02 (evolvability). Deliberate", second["deliverable"])
        self.assertIn("7 patterns, 21 occurrences in total", second["deliverable"])


class ModelsReadOnlyTest(helpers.TempDirCase):
    def test_x5_audit_start_writes_no_models_file(self):
        """X5 / MD-05: audit start resolves lab-models.json in memory and never
        writes .claude/models.json in the audited repository, failing or not."""
        (self.tmp / ".claude").mkdir()
        legacy = self.tmp / ".claude" / "lab-models.json"
        legacy.write_text('{"math": "fable"}\n', encoding="utf-8")
        args = helpers.write_output(self.tmp, "a.txt", "--lenses fidelite;bio")
        out = check_ok(self, helpers.harness(["audit", "start", "--skill", "ai-lab-audit", "--arguments-file", args],
                                             self.tmp))
        self.assertEqual({a["auditor"]: a["model"] for a in out["auditors"]}, {"fidelite": "fable", "bio": "inherit"})
        self.assertFalse((self.tmp / ".claude" / "models.json").exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), '{"math": "fable"}\n')
        legacy.write_text('{"math": "gpt"}\n', encoding="utf-8")
        args = helpers.write_output(self.tmp, "a.txt", "")
        res = helpers.harness(["audit", "start", "--skill", "ai-lab-audit", "--arguments-file", args], self.tmp)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertFalse((self.tmp / ".claude" / "models.json").exists())


class FactoryRunTransitTest(helpers.TempDirCase):
    def test_fr01_factory_run_start_removes_its_transit_file(self):
        """FR-01: factory-run start removes .harness/runs/factory-run-arguments.txt once
        read, the command succeeded or not."""
        args = helpers.write_output(self.tmp, "factory-run-arguments.txt", "a need\n")
        check_ok(self, helpers.harness(["factory-run", "start", "--arguments-file", args], self.tmp))
        self.assertFalse((self.tmp / args).exists())
        args = helpers.write_output(self.tmp, "factory-run-arguments.txt", 'need --models "nope=opus"\n')
        res = helpers.harness(["factory-run", "start", "--arguments-file", args], self.tmp)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertFalse((self.tmp / args).exists())


if __name__ == "__main__":
    unittest.main()
