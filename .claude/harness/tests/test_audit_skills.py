# The deterministic rules of the three audits, skill by skill (AA-01 to AA-14,
# LA-01 to LA-13, VA-01 to VA-13), on top of the shared engine (test_audit.py).

import json
import unittest
from pathlib import Path

import helpers
from hcore import audit, findings, gates, models, registry, tshirt
from test_audit import EngineCase, block, confirmed, lab_item, legacy_item, vib_item

SECTIONS = {"context": "c", "scope": "s", "criteria": ["a testable criterion"], "out_of_scope": "o", "risks": "r"}


def verdict(**fields):
    return json.dumps(dict({"verdict": "CONFIRMED", "reason": "r"}, **fields))


def run_findings(test, skill, args, units):
    """Starts a run and feeds it the auditor outputs (unit -> list of items) and
    a CONFIRMED verdict for every proposed finding."""
    state, reg = test.start(skill, args)
    for unit, items in units.items():
        audit.record_auditor(state, reg, unit, block({"findings": items}), False)
    for fid in list(state["findings"]):
        if state["findings"][fid]["status"] == findings.PROPOSED:
            audit.record_validator(state, reg, fid, confirmed(), False)
    return state, reg


class AiLabAuditTest(EngineCase):
    def test_aa01_lenses_prefixes_and_bio_removal(self):
        """AA-01: five lenses with their prefixes; a run without `bio` launches four auditors."""
        p = audit.profile("ai-lab-audit")
        self.assertEqual([(a.name, a.prefix) for a in p.auditors],
                         [("fidelite", "FID"), ("validite", "VAL"), ("fuites", "LEAK"), ("bio", "BIO"), ("route", "ROAD")])
        parsed = audit.parse_arguments("ai-lab-audit", '--lenses "fidelite;validite;fuites;route"', self.tmp)
        self.assertEqual(parsed["auditors"], ["fidelite", "validite", "fuites", "route"])
        state, _ = self.start("ai-lab-audit", '--lenses "fidelite;route"')
        self.assertEqual(sorted(state["auditors"]), ["fidelite", "route"])

    def test_aa02_max_questions(self):
        """AA-02: min(N, 12), default 6; the excess goes to 'not retained' in the given order."""
        self.assertEqual(audit.parse_arguments("ai-lab-audit", "", self.tmp)["cap"], 6)
        self.assertEqual(audit.parse_arguments("ai-lab-audit", "--max-questions 40", self.tmp)["cap"], 12)
        capped = tshirt.apply_cap(["Q-01", "Q-02", "Q-03"], 2)
        self.assertEqual(capped, {"kept": ["Q-01", "Q-02"], "excess": ["Q-03"]})
        with self.assertRaises(tshirt.SizeError):
            tshirt.apply_cap(["Q-01"], 0)

    def test_aa03_models_roles_and_aliases(self):
        """AA-03: roles neuro, math, director.cadrage, validator (aliases of lab.*); anything else is refused."""
        p = audit.profile("ai-lab-audit")
        parsed = audit.parse_arguments("ai-lab-audit", '--models "math=fable;validator=haiku;director.cadrage=opus"',
                                       self.tmp)
        resolved = models.resolve_readonly(self.tmp, p.roles, parsed["models"], p.aliases)
        self.assertEqual({r: e["model"] for r, e in resolved["roles"].items()},
                         {"lab.neuro": "inherit", "lab.math": "fable", "lab.director.cadrage": "opus",
                          "lab.validator": "haiku"})
        for bad in ("builder=fable", "math=gpt"):
            with self.assertRaises(models.ModelsError):
                models.resolve_readonly(self.tmp, p.roles, bad, p.aliases)
        self.assertFalse((self.tmp / ".claude").exists())

    def test_aa04_writes_limited_to_the_audit_and_lab_specs(self):
        """AA-04: AUDIT-lab.md and specs/SPEC-lab-*.md (plus the run files); anything else is refused."""
        repo = self.tmp
        for ok in ("AUDIT-lab.md", "specs/SPEC-lab-01-e2.md", ".harness/runs/x.jsonl"):
            self.assertEqual(audit.check_writable("ai-lab-audit", repo, repo / ok), ok)
        for bad in ("docs/JOURNAL.md", "specs/SPEC-leg-01-x.md", "src/model.py", "../outside.md"):
            with self.assertRaises(audit.AuditUsage):
                audit.check_writable("ai-lab-audit", repo, repo / bad)

    def test_aa05_lab_presence_and_subagent_type(self):
        """AA-05: LAB needs the three lab agents and lab-run; fidelite, validite, fuites -> lab-math, bio -> lab-neuro, route -> lab-director, else Explore."""
        p = audit.profile("ai-lab-audit")
        self.assertFalse(audit.lab_present(self.tmp))
        for name in ("lab-director", "lab-neuro", "lab-math"):
            helpers_write(self.tmp / ".claude" / "agents" / ("%s.md" % name))
        self.assertFalse(audit.lab_present(self.tmp))
        helpers_write(self.tmp / ".claude" / "skills" / "lab-run" / "SKILL.md")
        self.assertTrue(audit.lab_present(self.tmp))
        got = {a.name: audit.subagent_type(p, a.name, True) for a in p.auditors}
        self.assertEqual(got, {"fidelite": "lab-math", "validite": "lab-math", "fuites": "lab-math",
                               "bio": "lab-neuro", "route": "lab-director"})
        self.assertEqual({audit.subagent_type(p, a.name, False) for a in p.auditors}, {"Explore"})
        out = helpers.harness(["audit", "start", "--skill", "ai-lab-audit"], self.tmp)
        started = json.loads(out.stdout)
        self.assertTrue(started["lab"])
        self.assertEqual([a["subagent_type"] for a in started["auditors"]],
                         ["lab-math", "lab-math", "lab-math", "lab-neuro", "lab-director"])

    def test_aa06_lens_to_model_role(self):
        """AA-06: math for fidelite, validite, fuites; neuro for bio; director.cadrage for route; validator for the validators."""
        p = audit.profile("ai-lab-audit")
        self.assertEqual({a.name: audit.auditor_role(p, a.name) for a in p.auditors},
                         {"fidelite": "lab.math", "validite": "lab.math", "fuites": "lab.math", "bio": "lab.neuro",
                          "route": "lab.director.cadrage"})
        self.assertEqual(p.validator_role, "lab.validator")

    def test_aa07_auditor_schema_nature_and_journal_date(self):
        """AA-07: nature is mandatory (correction|question), journal_date goes into the evidence; ids follow the prefixes."""
        state, reg = self.start("ai-lab-audit")
        bad = dict(lab_item(), nature="opinion")
        self.assertEqual(audit.record_auditor(state, reg, "fidelite", block({"findings": [bad]}), False)[0]["action"],
                         "relaunch")
        item = lab_item("ROAD-01", "route", "question", file="docs/journal.md", line=None, date="2026-09-01")
        audit.record_auditor(state, reg, "route", block({"findings": [item]}), False)
        f = state["findings"]["ROAD-01"]
        self.assertEqual(f["evidence"]["journal_date"], "2026-09-01")
        self.assertEqual(f["nature"], "question")

    def test_aa08_dedup_place_and_lens(self):
        """AA-08: same file and line, or same document and date, and same lens: duplicate; another lens: not."""
        p = audit.profile("ai-lab-audit")
        a = findings.adapt("lab-audit-auditor", lab_item("FID-01"), "FID-01", "fidelite", "ai-lab-audit")
        b = findings.adapt("lab-audit-auditor", lab_item("VAL-01", "validite"), "VAL-01", "validite", "ai-lab-audit")
        c = findings.adapt("lab-audit-auditor", lab_item("FID-02", title="Other words"), "FID-02", "fidelite",
                           "ai-lab-audit")
        self.assertEqual(audit.dedup_keys(p, a), audit.dedup_keys(p, c))
        self.assertNotEqual(audit.dedup_keys(p, a), audit.dedup_keys(p, b))
        d1 = findings.adapt("lab-audit-auditor", lab_item("VAL-02", "validite", "question", file="j.md", line=None,
                                                         date="2026-09-01"), "VAL-02", "validite", "ai-lab-audit")
        d2 = findings.adapt("lab-audit-auditor", lab_item("VAL-03", "validite", "question", file="j.md", line=None,
                                                         date="2026-09-02"), "VAL-03", "validite", "ai-lab-audit")
        self.assertNotEqual(audit.dedup_keys(p, d1), audit.dedup_keys(p, d2))

    def test_aa09_corrections_and_questions_numbering_and_sorts(self):
        """AA-09: COR-nn by severity desc then size asc; Q-nn by severity desc then cost asc."""
        state, _ = run_findings(self, "ai-lab-audit", "--lenses fidelite;validite", {
            "fidelite": [lab_item("FID-01", severity="major", size="M", title="a", line=1),
                         lab_item("FID-02", severity="major", size="XS", title="b", line=2),
                         lab_item("FID-03", severity="critical", size="L", title="c", line=3)],
            "validite": [lab_item("VAL-01", "validite", "question", file=None, line=None, severity="minor", size="L",
                                  title="q1"),
                         lab_item("VAL-02", "validite", "question", file=None, line=None, severity="critical", size="M",
                                  title="q2"),
                         lab_item("VAL-03", "validite", "question", file=None, line=None, severity="critical",
                                  size="XS", title="q3")]})
        renamed = audit.consolidate(state)["renamed"]
        self.assertEqual([renamed[i] for i in ("FID-03", "FID-02", "FID-01")], ["COR-01", "COR-02", "COR-03"])
        self.assertEqual([renamed[i] for i in ("VAL-03", "VAL-02", "VAL-01")], ["Q-01", "Q-02", "Q-03"])

    def test_aa10_settled_by_a_later_journal_entry(self):
        """AA-10: TRANCHEE (journal <date>) only when the entry is later than the audit date; dates are checked."""
        self.assertEqual(audit.settle_status("2026-09-01", "2026-09-10"), "TRANCHEE (journal 2026-09-10)")
        self.assertIsNone(audit.settle_status("2026-09-01", "2026-09-01"))
        self.assertIsNone(audit.settle_status("2026-09-10", "2026-09-01"))
        with self.assertRaises(audit.AuditUsage):
            audit.settle_status("2026-09-01", "2026-13-40")
        p = audit.profile("ai-lab-audit")
        self.assertEqual(audit.check_status(p, "TRANCHEE (journal 2026-09-10)"), "TRANCHEE (journal 2026-09-10)")
        res = helpers.harness(["audit", "settle", "--audit-date", "2026-09-01", "--entry-date", "2026-10-01"], self.tmp)
        self.assertEqual(json.loads(res.stdout)["status"], "TRANCHEE (journal 2026-10-01)")

    def test_aa11_aa14_audit_lab_rendering_and_report_table(self):
        """AA-11, AA-14: AUDIT-lab.md carries '/lab-run' for each retained question; the report table counts corrections and questions per lens."""
        state, reg = run_findings(self, "ai-lab-audit", "--lenses fidelite;validite", {
            "fidelite": [lab_item("FID-01", title="Mismatch")],
            "validite": [lab_item("VAL-01", "validite", "question", file=None, line=None, title="Single seed")]})
        data = {"name": "toy", "date": "2026-10-17", "lab": True, "factory": False, "lenses": ["fidelite", "validite"],
                "journal_date": "2026-09-01", "findings": {
                    "Q-01": {"question": "Does it hold on 5 seeds?", "motivation": "VAL-01", "decides": "the title"}}}
        text = audit.render("ai-lab-audit", audit.document(state, data))
        self.assertIn('- **Run**: `/lab-run "Does it hold on 5 seeds?"`', text)
        self.assertLess(text.index("## Corrections to make"), text.index("## Research questions retained"))
        rows = {r["unit"]: r for r in audit.counters(state)}
        self.assertEqual((rows["fidelite"]["corrections"], rows["validite"]["questions"]), (1, 1))
        self.assertEqual(set(rows["fidelite"]), {"unit", "raw", "confirmed", "rejected", "corrections", "questions",
                                                 "unvalidated"})

    def test_aa12_leak_specs_carry_the_two_imposed_criteria(self):
        """AA-12: a spec for a `fuites` correction always carries both imposed acceptance criteria."""
        state, _ = run_findings(self, "ai-lab-audit", "--lenses fuites", {
            "fuites": [lab_item("LEAK-01", "fuites", title="Budget differs")]})
        f = state["findings"]["LEAK-01"]
        out = audit.write_spec("ai-lab-audit", self.tmp, f, SECTIONS, 1, "budget")
        self.assertEqual(out["state"], "créé")
        text = (self.tmp / out["file"]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("Status: PROPOSED\n"))
        for criterion in findings.deliverable_texts()["leak_criteria"]:
            self.assertIn(criterion, text)
        self.assertEqual(len(findings.deliverable_texts()["leak_criteria"]), 2)

    def test_aa13_gates_and_headless_fallback(self):
        """AA-13: framing, selection and final gates; headless fallback stop; at most 2 selection calls of 4 themes."""
        for gid in ("ai-lab-audit.framing", "ai-lab-audit.selection", "ai-lab-audit.final"):
            self.assertEqual(gates.get(gid).headless, "stop")
        self.assertEqual(audit.gate_calls("ai-lab-audit", 8)["calls"], [[0, 1, 2, 3], [4, 5, 6, 7]])
        with self.assertRaises(audit.AuditUsage):
            audit.gate_calls("ai-lab-audit", 9)


def helpers_write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


class LegacyAuditTest(EngineCase):
    def test_la01_families_and_prefixes(self):
        """LA-01: hygiene, portabilite, qualite, filet with HYG, PORT, QUAL, FIL."""
        p = audit.profile("legacy-audit")
        self.assertEqual([(a.name, a.prefix) for a in p.auditors],
                         [("hygiene", "HYG"), ("portabilite", "PORT"), ("qualite", "QUAL"), ("filet", "FIL")])
        self.assertEqual(audit.parse_arguments("legacy-audit", "--categories filet", self.tmp)["auditors"], ["filet"])

    def test_la02_max_specs(self):
        """LA-02: min(N, 25), default 15; the specs over the ceiling go to 'non retenus', in the milestones' order."""
        self.assertEqual(audit.parse_arguments("legacy-audit", "", self.tmp)["cap"], 15)
        self.assertEqual(audit.parse_arguments("legacy-audit", "--max-specs 99", self.tmp)["cap"], 25)
        items = [{"id": "LEG-%02d" % i, "severity": "major", "size": "S", "title": "t%d" % i, "axis": "hygiene",
                  "source": "legacy-audit", "status": "CONFIRMED"} for i in range(1, 5)]
        specs = tshirt.plan_specs([dict(i, category="c", milestone=1) for i in items], "category-milestone")
        capped = tshirt.apply_cap(specs, 3)
        self.assertEqual(len(capped["kept"]), 3)
        self.assertEqual(capped["excess"][0]["findings"], ["LEG-04"])

    def test_la03_git_absent_adds_a_hygiene_pre_finding(self):
        """LA-03: GIT_ABSENT -> a versioning pre-finding in the hygiene family, validated like the others; with git, none."""
        parsed = audit.parse_arguments("legacy-audit", "", self.tmp)
        state = audit.new_state("legacy-audit", parsed, True, False, {}, None)
        reg = registry.Registry(self.tmp / ".harness" / "runs" / "r.registry.jsonl")
        d, log = audit.record_auditor(state, reg, "hygiene", block({"findings": []}), False)
        self.assertEqual([x["id"] for x in d["to_validate"]], ["HYG-01"])
        self.assertEqual(state["findings"]["HYG-01"]["category"], "versioning-absent")
        self.assertEqual(log["produced"], 1)
        d, _ = audit.record_auditor(state, reg, "portabilite", block({"findings": []}), False)
        self.assertEqual(d["to_validate"], [])
        state, reg = self.start("legacy-audit")
        d, _ = audit.record_auditor(state, reg, "hygiene", block({"findings": []}), False)
        self.assertEqual(d["to_validate"], [])

    def test_la04_factory_presence(self):
        """LA-04: `audit start` reports whether .claude/skills/factory-run/SKILL.md exists."""
        args = helpers.write_output(self.tmp, "a.txt", "")
        self.assertFalse(json.loads(helpers.harness(["audit", "start", "--skill", "legacy-audit", "--arguments-file",
                                                      args], self.tmp).stdout)["factory"])
        helpers_write(self.tmp / ".claude" / "skills" / "factory-run" / "SKILL.md")
        args = helpers.write_output(self.tmp, "a.txt", "")
        self.assertTrue(json.loads(helpers.harness(["audit", "start", "--skill", "legacy-audit", "--arguments-file",
                                                     args], self.tmp).stdout)["factory"])

    def test_la05_auditor_schema_and_critical_enumeration(self):
        """LA-05: severity is critical|major|minor, size XS..XL; a finding outside the enumerations makes the output unusable."""
        state, reg = self.start("legacy-audit")
        for bad in (dict(legacy_item(), severity="blocker"), dict(legacy_item(), size="XXL")):
            state, reg = self.start("legacy-audit")
            self.assertEqual(audit.record_auditor(state, reg, "hygiene", block({"findings": [bad]}), False)[0]["action"],
                             "relaunch")
        state, reg = self.start("legacy-audit")
        self.assertEqual(audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)[0]
                         ["action"], "validate")

    def test_la06_dedup_file_line_category(self):
        """LA-06: same file, line and category: duplicate; another category or line: not."""
        p = audit.profile("legacy-audit")
        mk = lambda fid, **kw: findings.adapt("legacy-auditor", legacy_item(fid, **kw), fid, "hygiene",  # noqa: E731
                                              "legacy-audit")
        base = mk("HYG-01")
        self.assertEqual(audit.dedup_keys(p, base), audit.dedup_keys(p, mk("HYG-02", title="Another title")))
        self.assertNotEqual(audit.dedup_keys(p, base), audit.dedup_keys(p, mk("HYG-03", category="config")))
        self.assertNotEqual(audit.dedup_keys(p, base), audit.dedup_keys(p, mk("HYG-04", line=99)))

    def test_la07_validator_size_correction(self):
        """LA-07: the validator verdict carries size_correction, applied to the finding; an invalid size is unusable."""
        state, reg = self.start("legacy-audit")
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item(size="S")]}), False)
        audit.record_validator(state, reg, "HYG-01", verdict(size_correction="XS"), False)
        self.assertEqual(state["findings"]["HYG-01"]["size"], "XS")
        state, reg = self.start("legacy-audit")
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)
        self.assertEqual(audit.record_validator(state, reg, "HYG-01", verdict(size_correction="HUGE"), False)[0]
                         ["action"], "relaunch")

    def test_la08_leg_numbering_and_sort(self):
        """LA-08: LEG-nn by severity desc then size asc."""
        state, _ = run_findings(self, "legacy-audit", "--categories hygiene;qualite", {
            "hygiene": [legacy_item("HYG-01", severity="major", size="M", title="a", line=1),
                        legacy_item("HYG-02", severity="critical", size="L", title="b", line=2)],
            "qualite": [legacy_item("QUAL-01", severity="major", size="XS", title="c", line=3, category="dead")]})
        renamed = audit.consolidate(state)["renamed"]
        self.assertEqual([renamed[i] for i in ("HYG-02", "QUAL-01", "HYG-01")], ["LEG-01", "LEG-02", "LEG-03"])

    def test_la09_roadmap_rendering_statuses_and_links(self):
        """LA-09: milestones with tables, relative links with '/', statuses A_FAIRE..REPORTE only, 'not retained' section."""
        state, _ = run_findings(self, "legacy-audit", "--categories hygiene", {
            "hygiene": [legacy_item("HYG-01"), legacy_item("HYG-02", title="Other", line=9, category="x")]})
        data = {"name": "toy", "date": "2026-10-17", "factory": True, "milestones": [{"number": 1, "title": "Assainir"}],
                "findings": {"LEG-01": {"milestone": 1, "spec": "specs\\SPEC-leg-01-purge.md"},
                             "LEG-02": {"not_retained": "cap"}}}
        text = audit.render("legacy-audit", audit.document(state, data))
        self.assertIn("## Milestone 1 — Assainir", text)
        self.assertIn("| [specs/SPEC-leg-01-purge.md](specs/SPEC-leg-01-purge.md) | A_FAIRE |", text)
        self.assertNotIn("\\", text)
        self.assertIn("| LEG-02 | Other | beyond the spec cap |", text)
        p = audit.profile("legacy-audit")
        for ok in ("A_FAIRE", "EN_COURS", "FAIT", "REPORTE"):
            self.assertEqual(audit.check_status(p, ok), ok)
        with self.assertRaises(audit.AuditUsage):
            audit.check_status(p, "TRANCHEE (journal 2026-09-10)")
        with self.assertRaises(audit.AuditUsage):
            audit.render("legacy-audit", audit.document(state, dict(data, findings={"LEG-01": {"milestone": 9}})))

    def test_la10_finding_to_spec_mapping(self):
        """LA-10: 1 finding = 1 spec; XS of one category and milestone grouped by 4; an XL gives >= 2 chained specs <= L."""
        fs = [{"id": "LEG-%02d" % i, "size": "XS", "category": "c", "milestone": 1} for i in range(1, 6)]
        fs.append({"id": "LEG-06", "size": "XL", "category": "d", "milestone": 1})
        fs.append({"id": "LEG-07", "size": "M", "category": "e", "milestone": 2})
        specs = tshirt.plan_specs(fs, "category-milestone")
        self.assertEqual([len(s["findings"]) for s in specs[:2]], [4, 1])
        xl = [s for s in specs if s["findings"] == ["LEG-06"]]
        self.assertGreaterEqual(len(xl), 2)
        self.assertTrue(all(s["max_size"] == "L" for s in xl))
        self.assertEqual(xl[1]["depends_on"], specs.index(xl[0]))
        self.assertEqual(specs[-1]["findings"], ["LEG-07"])

    def test_la12_gates_assignment_calls_and_headless_stop(self):
        """LA-12: framing, assignment (3 calls of 4 themes at most) and final gates; headless fallback stop."""
        for gid in ("legacy-audit.framing", "legacy-audit.assignment", "legacy-audit.final"):
            self.assertEqual(gates.get(gid).headless, "stop")
        self.assertEqual(len(audit.gate_calls("legacy-audit", 12)["calls"]), 3)
        with self.assertRaises(audit.AuditUsage):
            audit.gate_calls("legacy-audit", 13)

    def test_la13_report_table_per_family(self):
        """LA-13: raw, confirmed and rejected counters per family."""
        state, reg = self.start("legacy-audit", "--categories hygiene;filet")
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item(), legacy_item("HYG-02", title="b",
                                                                                                    line=8)]}), False)
        audit.record_validator(state, reg, "HYG-01", confirmed(), False)
        audit.record_validator(state, reg, "HYG-02", json.dumps({"verdict": "REJECTED", "reason": "r"}), False)
        audit.record_auditor(state, reg, "filet", None, True)
        audit.record_auditor(state, reg, "filet", None, True)
        rows = {r["unit"]: r for r in audit.counters(state)}
        self.assertEqual((rows["hygiene"]["raw"], rows["hygiene"]["confirmed"], rows["hygiene"]["rejected"]), (2, 1, 1))
        self.assertEqual(rows["filet"]["raw"], "failed")


class VibecodeAuditTest(EngineCase):
    def test_va01_axes_families_and_prefixes(self):
        """VA-01: acces, injection and metier belong to securite; prefixes ACC, INJ, MET, MNT, EVO."""
        p = audit.profile("vibecode-audit")
        self.assertEqual([(a.name, a.unit, a.prefix) for a in p.auditors],
                         [("acces", "securite", "ACC"), ("injection", "securite", "INJ"), ("metier", "securite", "MET"),
                          ("maintenabilite", "maintenabilite", "MNT"), ("evolutivite", "evolutivite", "EVO")])
        self.assertEqual(audit.parse_arguments("vibecode-audit", "--axes evolutivite", self.tmp)["auditors"],
                         ["evolutivite"])

    def test_va02_max_findings(self):
        """VA-02: min(N, 30), default 15; the excess goes to the short list."""
        self.assertEqual(audit.parse_arguments("vibecode-audit", "", self.tmp)["cap"], 15)
        self.assertEqual(audit.parse_arguments("vibecode-audit", "--max-findings 99", self.tmp)["cap"], 30)
        self.assertEqual(tshirt.apply_cap(["VIB-01", "VIB-02"], 1)["excess"], ["VIB-02"])

    def test_va03_only_the_report_is_written(self):
        """VA-03: AUDIT-vibecode.md only; no spec, no other file."""
        repo = self.tmp
        self.assertEqual(audit.check_writable("vibecode-audit", repo, repo / "AUDIT-vibecode.md"), "AUDIT-vibecode.md")
        for bad in ("specs/SPEC-leg-01-x.md", "ROADMAP-legacy.md", "src/app.js", ".gitignore"):
            with self.assertRaises(audit.AuditUsage):
                audit.check_writable("vibecode-audit", repo, repo / bad)
        with self.assertRaises(audit.AuditUsage):
            audit.write_spec("vibecode-audit", repo, {}, SECTIONS, 1, "x")

    def test_va04_occurrences_capped_at_five_with_the_real_count(self):
        """VA-04: 5 occurrences kept, count never below what was listed, sampled as soon as some are left out."""
        state, reg = self.start("vibecode-audit")
        many = vib_item("ACC-01", places=tuple(("f%d.js" % i, i + 1) for i in range(8)))
        many["count"] = 14
        audit.record_auditor(state, reg, "acces", block({"findings": [many]}), False)
        f = state["findings"]["ACC-01"]
        self.assertEqual((len(f["occurrences"]), f["count"], f["sampled"]), (5, 14, True))
        state, reg = self.start("vibecode-audit")
        under = vib_item("ACC-01", places=tuple(("f%d.js" % i, i + 1) for i in range(7)))
        under["count"] = 3
        audit.record_auditor(state, reg, "acces", block({"findings": [under]}), False)
        self.assertEqual(state["findings"]["ACC-01"]["count"], 7)
        state, reg = self.start("vibecode-audit")
        audit.record_auditor(state, reg, "acces", block({"findings": [vib_item("ACC-01", places=(("a.js", 1),))]}), False)
        self.assertEqual((state["findings"]["ACC-01"]["count"], state["findings"]["ACC-01"]["sampled"]), (1, False))

    def test_va05_va06_dead_family_is_a_major_surface_announced_in_the_report(self):
        """VA-05, VA-06: dead family twice -> unauditable surface major, raw = failed, announced at the top; a surface below major is refused."""
        state, reg = self.start("vibecode-audit", "--axes evolutivite")
        audit.record_auditor(state, reg, "evolutivite", None, True)
        d, _ = audit.record_auditor(state, reg, "evolutivite", "garbage", False)
        self.assertEqual((d["action"], d["severity"]), ("surface", "major"))
        self.assertEqual([r for r in audit.counters(state)][0]["raw"], "failed")
        data = {"name": "poc", "date": "2026-10-17", "axes": ["evolutivite"], "path": "./poc", "findings": {}}
        text = audit.render("vibecode-audit", audit.document(state, data))
        self.assertIn("**Family not audited: evolutivite.**", text)
        self.assertIn("| family evolutivite |", text)
        weak = dict(data, surfaces=[{"surface": "s", "why": "w", "needs": "n", "severity": "minor"}])
        with self.assertRaises(audit.AuditUsage):
            audit.render("vibecode-audit", audit.document(state, weak))
        strong = dict(data, surfaces=[{"surface": "s2", "why": "w", "needs": "n", "severity": "critical"}])
        self.assertIn("| s2 |", audit.render("vibecode-audit", audit.document(state, strong)))

    def test_va08_split_false_occurrences_and_axis_reclassification(self):
        """VA-08: false_occurrences are removed and count recomputed; axis_correction is applied; SPLIT builds the parts."""
        state, reg = self.start("vibecode-audit")
        audit.record_auditor(state, reg, "acces", block({"findings": [
            vib_item("ACC-01", places=(("a.js", 1), ("b.js", 2), ("c.js", 3)))]}), False)
        audit.record_validator(state, reg, "ACC-01", verdict(axis_correction="evolutivite",
                                                             false_occurrences=["b.js:2"]), False)
        f = state["findings"]["ACC-01"]
        self.assertEqual(f["axis"], "evolutivite")
        self.assertEqual(f["count"], 2)
        self.assertEqual([o["file"] for o in f["occurrences"]], ["a.js", "c.js"])
        state, reg = self.start("vibecode-audit")
        audit.record_auditor(state, reg, "acces", block({"findings": [
            vib_item("ACC-01", places=(("a.js", 1), ("b.js", 2)))]}), False)
        d, _ = audit.record_validator(state, reg, "ACC-01", json.dumps({
            "verdict": "SPLIT", "reason": "two defects", "split_into": [
                {"pattern": "first", "occurrences": ["a.js:1"], "severity": "major", "difficulty": "S"},
                {"pattern": "second", "occurrences": ["b.js:2"], "severity": "minor", "difficulty": "M"}]}), False)
        self.assertEqual((d["action"], d["parts"]), ("split", ["ACC-01.1", "ACC-01.2"]))

    def test_va09_partial_validation_critical_first_then_occurrences_and_marked(self):
        """VA-09: validators run critical first, then by occurrence count; an UNVALIDATED pattern is delivered marked 'not validated' with the ratio."""
        state, reg = self.start("vibecode-audit", "--axes securite")
        audit.record_auditor(state, reg, "acces", block({"findings": [
            vib_item("ACC-01", "few", (("a.js", 1),), severity="major"),
            vib_item("ACC-02", "many", tuple(("m%d.js" % i, 1) for i in range(5)), severity="major"),
            vib_item("ACC-03", "crit", (("c.js", 1),), severity="critical")]}), False)
        self.assertEqual(audit.next_step(state)["pending_findings"], ["ACC-03", "ACC-02", "ACC-01"])
        audit.record_validator(state, reg, "ACC-03", confirmed(), False)
        audit.record_validator(state, reg, "ACC-02", confirmed(), False)
        audit.record_validator(state, reg, "ACC-01", None, True)
        audit.record_validator(state, reg, "ACC-01", None, True)
        for unit in ("injection", "metier"):
            audit.record_auditor(state, reg, unit, block({"findings": []}), False)
        data = {"name": "poc", "date": "2026-10-17", "axes": ["securite"], "path": ".", "findings": {}}
        text = audit.render("vibecode-audit", audit.document(state, data))
        self.assertIn("| not validated |", text)
        self.assertIn("2 patterns out of 3 passed the adversarial validation", text)

    def test_va10_ordering_raised_criticals_and_quick_wins(self):
        """VA-10: every critical first whatever its axis, then securite > maintenabilite > evolutivite, then difficulty; regression_risk never sorts; raised criticals and quick wins are reported."""
        mk = lambda fid, axis, sev, diff, risk: findings.adapt(  # noqa: E731
            "vibecode-auditor", vib_item(fid, fid, (("a.js", 1),), sev, axis, diff, ) | {"regression_risk": risk},
            fid, None, "vibecode-audit")
        fs = [mk("ACC-01", "securite", "major", "S", "faible"), mk("EVO-01", "evolutivite", "critical", "XL", "eleve"),
              mk("MNT-01", "maintenabilite", "major", "XS", "eleve"), mk("MET-01", "securite", "major", "L", "faible")]
        order = [f["id"] for f in tshirt.ordered(fs, "VIB")]
        self.assertEqual(order, ["EVO-01", "ACC-01", "MET-01", "MNT-01"])
        shaken = [dict(f, regression_risk="faible" if f["regression_risk"] == "eleve" else "eleve") for f in fs]
        self.assertEqual([f["id"] for f in tshirt.ordered(shaken, "VIB")], order)
        self.assertEqual(tshirt.raised_criticals(tshirt.ordered(fs, "VIB")), ["EVO-01"])
        self.assertEqual(tshirt.quick_wins(tshirt.ordered(fs, "VIB")), ["ACC-01", "MNT-01"])

    def test_va12_report_resumed_without_comparing_states(self):
        """VA-12: a second render on a resumed report keeps its rows and statuses; the numbering goes on; no comparison section appears."""
        state, _ = run_findings(self, "vibecode-audit", "--axes securite",
                                {"acces": [vib_item("ACC-01")], "injection": [], "metier": []})
        data = {"name": "poc", "date": "2026-10-17", "axes": ["securite"], "path": ".", "findings": {}}
        first = audit.render("vibecode-audit", audit.document(state, data))
        edited = first.replace("| A_FAIRE |", "| FAIT |", 1)
        resumed = audit.parse_deliverable("vibecode-audit", edited)
        state2, reg2 = self.start("vibecode-audit", "--axes securite", resumed=resumed)
        audit.record_auditor(state2, reg2, "acces",
                             block({"findings": [vib_item("ACC-01", "Another pattern", (("z.js", 5),))]}), False)
        for unit in ("injection", "metier"):
            audit.record_auditor(state2, reg2, unit, block({"findings": []}), False)
        audit.record_validator(state2, reg2, "ACC-01", confirmed(), False)
        self.assertEqual(audit.consolidate(state2)["renamed"], {"ACC-01": "VIB-02"})
        second = audit.render("vibecode-audit", audit.document(state2, data))
        self.assertIn("| VIB-01 |", second)
        self.assertIn("| FAIT |", second)
        for word in ("évolution", "régression par rapport", "delta"):
            self.assertNotIn(word, second.lower() if word.islower() else second)

    def test_va13_report_table_per_family(self):
        """VA-13: raw, confirmed, split, rejected and confirmed occurrences per family."""
        state, _ = run_findings(self, "vibecode-audit", "--axes securite", {
            "acces": [vib_item("ACC-01", places=(("a.js", 1), ("b.js", 2)))]})
        row = audit.counters(state)[0]
        self.assertEqual((row["unit"], row["raw"], row["confirmed"], row["split"], row["occurrences"]),
                         ("acces", 1, 1, 0, 2))
        self.assertEqual([r["unit"] for r in audit.counters(state)], ["acces", "injection", "metier"])


class PlanTest(EngineCase):
    def test_la02_la10_plan_applies_the_ceiling_to_specs_in_milestone_order(self):
        """LA-02, LA-10, TS-05: --max-specs counts specs, in the milestones' order; the excess findings are listed; load per milestone."""
        items = [legacy_item("HYG-%02d" % i, title="t%d" % i, line=i, category="c%d" % i, size="S") for i in range(1, 5)]
        state, _ = run_findings(self, "legacy-audit", "--categories hygiene --max-specs 3", {"hygiene": items})
        ids = sorted(audit.consolidate(state)["renamed"].values())
        data = {"findings": {ids[0]: {"milestone": 2}, ids[1]: {"milestone": 1}, ids[2]: {"milestone": 2},
                             ids[3]: {"milestone": 1}}}
        got = audit.plan(state, data)
        self.assertEqual([s["findings"][0] for s in got["specs"]], [ids[1], ids[3], ids[0]])
        self.assertEqual(got["not_retained_cap"], [ids[2]])
        self.assertEqual(got["load"]["groups"]["1"]["count"], 2)
        with self.assertRaises(audit.AuditUsage):
            audit.plan(state, {"findings": {ids[0]: {"milestone": 1}}})
        data["findings"][ids[2]] = {"not_retained": "excluded"}
        self.assertEqual(audit.plan(state, data)["not_retained_cap"], [])

    def test_aa02_plan_caps_the_questions_in_the_arbitrated_order(self):
        """AA-02: --max-questions keeps the first questions of the arbitrated order; corrections get lens specs."""
        state, _ = run_findings(self, "ai-lab-audit", "--lenses fidelite;validite --max-questions 1", {
            "fidelite": [lab_item("FID-01")],
            "validite": [lab_item("VAL-01", "validite", "question", file=None, line=None, title="q1"),
                         lab_item("VAL-02", "validite", "question", file=None, line=None, title="q2", severity="minor")]})
        done = audit.consolidate(state)["order"]
        self.assertEqual(done["Q"], ["Q-01", "Q-02"])
        got = audit.plan(state, {"findings": {}})
        self.assertEqual((got["questions"], got["not_retained_cap"]), (["Q-01"], ["Q-02"]))
        self.assertEqual(len(got["specs"]), 1)
        flipped = audit.plan(state, {"findings": {}, "order": ["COR-01", "Q-02", "Q-01"]})
        self.assertEqual(flipped["questions"], ["Q-02"])

    def test_va02_plan_caps_the_detailed_patterns_and_lists_the_rest(self):
        """VA-02, VA-10: --max-findings counts detailed patterns; the excess goes to the short list; quick wins are reported."""
        state, _ = run_findings(self, "vibecode-audit", "--axes securite --max-findings 1", {
            "acces": [vib_item("ACC-01", "p1", (("a.js", 1),), difficulty="XS"),
                      vib_item("ACC-02", "p2", (("b.js", 1),), severity="minor")], "injection": [], "metier": []})
        got = audit.plan(state, {"findings": {}})
        self.assertEqual((got["detailed"], got["short_cap"]), (["VIB-01"], ["VIB-02"]))
        self.assertEqual(got["quick_wins"], ["VIB-01"])


if __name__ == "__main__":
    unittest.main()
