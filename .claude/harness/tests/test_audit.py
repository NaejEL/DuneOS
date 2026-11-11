# Shared audit engine hcore.audit and the 'audit' CLI (AU-01 to AU-16),
# with the scripted replays of the three audits on frozen findings (CA-21,
# CA-27 partly, CA-29, CA-30).

import json
import random
import unittest
from pathlib import Path

import helpers
from hcore import audit, findings, journal, registry, tshirt

AUDIT_FIXTURES = helpers.FIXTURES / "audit"
SKILLS = ("legacy-audit", "ai-lab-audit", "vibecode-audit")


def check_ok(test, res):
    test.assertEqual(res.returncode, 0, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout)


def block(obj):
    return "Report.\n\n```json\n%s\n```\n" % json.dumps(obj, ensure_ascii=False)


def legacy_item(fid="HYG-01", title="Secret in config.py", file="config.py", line=4, category="secrets",
                severity="critical", size="S"):
    return {"id": fid, "category": category, "severity": severity, "title": title,
            "location": {"file": file, "line": line}, "evidence": "KEY = 'x'", "impact": "leak",
            "remediation": "move it", "size": size, "depends_on": []}


def vib_item(fid="ACC-01", pattern="No ownership check", places=(("a.js", 1),), severity="critical",
             axis="securite", difficulty="M"):
    return {"id": fid, "axis": axis, "pattern": pattern, "severity": severity,
            "occurrences": [{"file": f, "line": n, "evidence": "x"} for f, n in places],
            "count": len(places), "sampled": False, "impact": "i", "remediation": "r",
            "difficulty": difficulty, "regression_risk": "moyen", "risk_rationale": "rr", "depends_on": []}


def lab_item(fid="FID-01", lens="fidelite", nature="correction", file="src/m.py", line=3, date=None,
             severity="major", size="S", title="Mismatch"):
    return {"id": fid, "lens": lens, "nature": nature, "severity": severity, "title": title,
            "location": {"file": file, "line": line, "journal_date": date}, "evidence": "e",
            "impact": "i", "proposal": "p", "size": size, "depends_on": []}


def confirmed(reason="ok"):
    return json.dumps({"verdict": "CONFIRMED", "reason": reason})


class EngineCase(helpers.TempDirCase):
    def start(self, skill, arguments="", project=None, resumed=None):
        parsed = audit.parse_arguments(skill, arguments, self.tmp)
        state = audit.new_state(skill, parsed, False, False, project or {}, resumed)
        reg = registry.Registry(self.tmp / ".harness" / "runs" / ("%s.registry.jsonl" % skill))
        return state, reg


# ------------------------------------------------------------ AU-01 to AU-03

class ArgumentsTest(helpers.TempDirCase):
    def test_au01_repo_is_the_first_token_absolute_default_cwd(self):
        """AU-01: the first token not starting with '--' is REPO, absolute; default:
        the current directory; a quoted path with spaces and an apostrophe is one token."""
        sub = self.tmp / "my O'Brien repo"
        sub.mkdir()
        parsed = audit.parse_arguments("legacy-audit", '"my O\'Brien repo" --max-specs 3', self.tmp)
        self.assertEqual(parsed["repo"], str(sub.resolve()))
        self.assertTrue(Path(parsed["repo"]).is_absolute())
        self.assertEqual(audit.parse_arguments("vibecode-audit", "", self.tmp)["repo"], str(self.tmp.resolve()))
        self.assertEqual(audit.parse_arguments("ai-lab-audit", "--lenses bio", self.tmp)["repo"],
                         str(self.tmp.resolve()))
        with self.assertRaises(audit.AuditUsage):
            audit.parse_arguments("legacy-audit", "repo extra", self.tmp)

    def test_au01_ca07_missing_path_is_an_explicit_stop_in_english(self):
        """AU-01, CA-07: a missing path stops (exit 1) with 'does not exist'; nothing is written."""
        with self.assertRaises(audit.AuditStop) as ctx:
            audit.parse_arguments("legacy-audit", "nowhere", self.tmp)
        self.assertIn("does not exist", str(ctx.exception))
        args = helpers.write_output(self.tmp, "args.txt", "nowhere --max-specs 2")
        res = helpers.harness(["audit", "start", "--skill", "legacy-audit", "--arguments-file", args], self.tmp)
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("the path nowhere does not exist", res.stderr)
        self.assertEqual(list((self.tmp / ".harness" / "runs").iterdir()), [])

    def test_au02_lists_separated_by_semicolons_default_all_unknown_exit_2(self):
        """AU-02: --categories / --lenses / --axes split on ';', default = all;
        unknown, empty or repeated value -> exit 2 (Q-J); vibecode axes select families."""
        p = audit.parse_arguments("legacy-audit", '--categories "qualite;hygiene"', self.tmp)
        self.assertEqual(p["units"], ["hygiene", "qualite"])
        self.assertEqual(audit.parse_arguments("legacy-audit", "", self.tmp)["units"],
                         ["hygiene", "portabilite", "qualite", "filet"])
        self.assertEqual(audit.parse_arguments("ai-lab-audit", "", self.tmp)["units"],
                         ["fidelite", "validite", "fuites", "bio", "route"])
        v = audit.parse_arguments("vibecode-audit", "--axes securite", self.tmp)
        self.assertEqual(v["auditors"], ["acces", "injection", "metier"])
        for skill, arg in (("legacy-audit", "--categories nope"), ("ai-lab-audit", '--lenses "bio;bio"'),
                           ("vibecode-audit", '--axes ";"'), ("legacy-audit", "--categories"),
                           ("legacy-audit", "--unknown x")):
            with self.assertRaises(audit.AuditUsage, msg=arg):
                audit.parse_arguments(skill, arg, self.tmp)
        args = helpers.write_output(self.tmp, "args.txt", "--lenses fuites;nope")
        res = helpers.harness(["audit", "start", "--skill", "ai-lab-audit", "--arguments-file", args], self.tmp)
        self.assertEqual(res.returncode, 2, res.stderr)

    def test_au03_caps_min_n_cap_with_defaults(self):
        """AU-03: min(N, cap), default when absent: legacy 25/15, ai-lab-audit 12/6,
        vibecode 30/15; N not an integer or < 1 -> exit 2."""
        for skill, option, cap, default in (("legacy-audit", "--max-specs", 25, 15),
                                            ("ai-lab-audit", "--max-questions", 12, 6),
                                            ("vibecode-audit", "--max-findings", 30, 15)):
            self.assertEqual(audit.parse_arguments(skill, "", self.tmp)["cap"], default)
            self.assertEqual(audit.parse_arguments(skill, "%s 99" % option, self.tmp)["cap"], cap)
            self.assertEqual(audit.parse_arguments(skill, "%s 2" % option, self.tmp)["cap"], 2)
            for bad in ("0", "-1", "abc", "2.5"):
                with self.assertRaises(audit.AuditUsage, msg=(skill, bad)):
                    audit.parse_arguments(skill, "%s %s" % (option, bad), self.tmp)
        args = helpers.write_output(self.tmp, "args.txt", "--max-findings 0")
        res = helpers.harness(["audit", "start", "--skill", "vibecode-audit", "--arguments-file", args], self.tmp)
        self.assertEqual(res.returncode, 2, res.stderr)


# --------------------------------------------------------------- AU-04 to AU-08

class AuditorTest(EngineCase):
    def test_au04_dead_family_relaunched_once_then_not_audited(self):
        """AU-04: dead or unusable -> one relaunch, then 'not audited' with a failed raw
        count (legacy-audit, ai-lab-audit)."""
        for skill, unit in (("legacy-audit", "qualite"), ("ai-lab-audit", "bio")):
            state, reg = self.start(skill)
            d, log = audit.record_auditor(state, reg, unit, None, True)
            self.assertEqual((d["action"], log["verdict"]), ("relaunch", "DEAD"))
            d, _ = audit.record_auditor(state, reg, unit, "no block at all", False)
            self.assertEqual(d["action"], "not-audited")
            self.assertIn(unit, audit.next_step(state)["not_audited"])
            row = [r for r in audit.counters(state) if r["unit"] == unit][0]
            self.assertEqual(row["raw"], audit.texts()["common"]["failed"])
            with self.assertRaises(audit.AuditUsage):
                audit.record_auditor(state, reg, unit, block({"findings": []}), False)

    def test_au04_vibecode_dead_family_is_an_unauditable_surface_major(self):
        """AU-04: vibecode-audit: second failure -> 'unauditable surface' of severity major."""
        state, reg = self.start("vibecode-audit")
        audit.record_auditor(state, reg, "injection", None, True)
        d, _ = audit.record_auditor(state, reg, "injection", block({"findings": "oops"}), False)
        self.assertEqual((d["action"], d["severity"]), ("surface", "major"))
        self.assertEqual(state["surfaces"][0]["auditor"], "injection")

    def test_au04_an_invalid_finding_makes_the_output_unusable(self):
        """AU-04: a finding the adapter refuses (no severity) or a vibecode axis
        outside the three axes is an unusable output, hence a relaunch."""
        state, reg = self.start("legacy-audit")
        bad = dict(legacy_item(), severity="huge")
        self.assertEqual(audit.record_auditor(state, reg, "hygiene", block({"findings": [bad]}), False)[0]["action"],
                         "relaunch")
        state, reg = self.start("vibecode-audit")
        self.assertEqual(audit.record_auditor(state, reg, "acces", block({"findings": [vib_item(axis="ux")]}),
                                              False)[0]["action"], "relaunch")

    def test_au05_validator_dead_twice_unvalidated_never_confirmed(self):
        """AU-05: a validator dead twice leaves its finding UNVALIDATED, never CONFIRMED."""
        state, reg = self.start("legacy-audit", "--categories hygiene")
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)
        self.assertEqual(audit.record_validator(state, reg, "HYG-01", None, True)[0]["action"], "relaunch")
        d, _ = audit.record_validator(state, reg, "HYG-01", "CONFIRMED, trust me", False)
        self.assertEqual(d["action"], "unvalidated")
        self.assertEqual(state["findings"]["HYG-01"]["status"], findings.UNVALIDATED)
        self.assertEqual(reg.current(registry.finding_key("config.py", "Secret in config.py")), "UNVALIDATED")
        self.assertEqual(audit.next_step(state)["step"], "report")
        with self.assertRaises(audit.AuditUsage):
            audit.record_validator(state, reg, "HYG-01", confirmed(), False)

    def test_au06_dedup_against_run_resume_and_project_with_each_skill_key(self):
        """AU-06: before any validator, a duplicate of the run (skill key), of the
        resumed deliverable (title) or of a REJECTED / SETTLED project entry is set
        aside; FIXED in the project is passed on as history, not dismissed."""
        # legacy-audit: same file, line and category.
        state, reg = self.start("legacy-audit")
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)
        twin = legacy_item("PORT-01", title="Other title", category="secrets")
        other = legacy_item("PORT-02", title="Other title 2", category="config")
        d, log = audit.record_auditor(state, reg, "portabilite", block({"findings": [twin, other]}), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["PORT-01"])
        self.assertEqual([x["id"] for x in d["to_validate"]], ["PORT-02"])
        self.assertEqual(log["rejected"], 1)
        self.assertEqual(state["findings"]["PORT-01"]["status"], findings.DUPLICATE)
        # ai-lab-audit: same document and date, and same lens; another lens is not a duplicate.
        state, reg = self.start("ai-lab-audit")
        j = dict(file="docs/journal.md", line=None, date="2026-09-01")
        d, _ = audit.record_auditor(state, reg, "validite", block({"findings": [
            lab_item("VAL-01", "validite", "question", title="A", **j),
            lab_item("VAL-02", "validite", "question", title="B", **j)]}), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["VAL-02"])
        d, _ = audit.record_auditor(state, reg, "route", block({"findings": [
            lab_item("ROAD-01", "route", "correction", title="C", **j)]}), False)
        self.assertEqual(d["duplicates"], [])
        # vibecode-audit: same set of occurrences -> dismissed within one auditor;
        # the same pattern from another auditor is reported for a merge (VA-07).
        state, reg = self.start("vibecode-audit")
        d, _ = audit.record_auditor(state, reg, "acces", block({"findings": [
            vib_item("ACC-01", "P1", (("a.js", 1),)), vib_item("ACC-02", "P2", (("a.js", 1),))]}), False)
        self.assertEqual([x["id"] for x in d["duplicates"]], ["ACC-02"])
        d, _ = audit.record_auditor(state, reg, "metier", block({"findings": [vib_item("MET-01", "p1", (("b.js", 2),))]}),
                                    False)
        self.assertEqual(d["merge"], [{"finding": "MET-01", "with": "ACC-01"}])
        # Resumed deliverable (title) and project registry (RG-04).
        resumed = audit.parse_deliverable("legacy-audit", (AUDIT_FIXTURES / "resume" / "roadmap.md")
                                          .read_text(encoding="utf-8")) if helpers.HAS_FIXTURES else None
        project = {registry.finding_key("a.py", "Rejected before"): {"status": "REJECTED", "source": "ship-pr"},
                   registry.finding_key("b.py", "Fixed before"): {"status": "FIXED", "source": "ship-pr"}}
        state, reg = self.start("legacy-audit", project=project, resumed=resumed)
        items = [legacy_item("HYG-01", "Rejected before", "a.py"), legacy_item("HYG-02", "Fixed before", "b.py")]
        if resumed:
            items.append(legacy_item("HYG-03", "Pas de .gitignore", "x.py", 9, "gitignore"))
        d, _ = audit.record_auditor(state, reg, "hygiene", block({"findings": items}), False)
        dismissed = {x["id"]: x["reason"] for x in d["duplicates"]}
        self.assertEqual(dismissed["HYG-01"], "previous=REJECTED@ship-pr")
        self.assertEqual(state["findings"]["HYG-01"]["previous"], "REJECTED@ship-pr")
        self.assertEqual([x["id"] for x in d["to_validate"]], ["HYG-02"])
        self.assertEqual(d["to_validate"][0]["previous"], "FIXED@ship-pr")
        if resumed:
            self.assertIn("LEG-03", dismissed["HYG-03"])

    def test_au07_zero_confirmed_goes_straight_to_the_report(self):
        """AU-07: zero CONFIRMED -> report phase (vibecode-audit: the deliverable,
        which keeps the unauditable surfaces); otherwise consolidate."""
        state, reg = self.start("legacy-audit", '--categories hygiene')
        self.assertEqual(audit.next_step(state)["step"], "audit")
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)
        self.assertEqual(audit.next_step(state)["step"], "validate")
        audit.record_validator(state, reg, "HYG-01", json.dumps({"verdict": "REJECTED", "reason": "fixture"}), False)
        self.assertEqual(audit.next_step(state)["step"], "report")
        state, reg = self.start("vibecode-audit", "--axes evolutivite")
        audit.record_auditor(state, reg, "evolutivite", block({"findings": []}), False)
        self.assertEqual(audit.next_step(state)["step"], "deliverable")
        state, reg = self.start("ai-lab-audit", "--lenses fidelite")
        audit.record_auditor(state, reg, "fidelite", block({"findings": [lab_item()]}), False)
        audit.record_validator(state, reg, "FID-01", confirmed(), False)
        self.assertEqual(audit.next_step(state)["step"], "consolidate")

    def test_au08_renumbering_per_prefix_after_ordering_and_resume_continues(self):
        """AU-08: CONFIRMED renumbered after the TS-03 ordering (LEG; COR and Q;
        VIB), depends_on follows; with a resumed deliverable the numbering goes on."""
        state, reg = self.start("ai-lab-audit", "--lenses fidelite;validite")
        audit.record_auditor(state, reg, "fidelite", block({"findings": [
            lab_item("FID-01", severity="minor", size="XS", title="a"),
            lab_item("FID-02", severity="critical", size="M", title="b", line=9)]}), False)
        q = dict(lab_item("VAL-01", "validite", "question", file=None, line=None, title="q"), depends_on=["FID-02"])
        audit.record_auditor(state, reg, "validite", block({"findings": [q]}), False)
        for fid in ("FID-01", "FID-02", "VAL-01"):
            audit.record_validator(state, reg, fid, confirmed(), False)
        result = audit.consolidate(state)
        self.assertEqual(result["renamed"], {"FID-02": "COR-01", "FID-01": "COR-02", "VAL-01": "Q-01"})
        self.assertEqual(state["findings"]["Q-01"]["depends_on"], ["COR-01"])
        self.assertIs(audit.consolidate(state), result)
        state, reg = self.start("legacy-audit", "--categories hygiene",
                                resumed={"entries": [], "numbering": {"LEG": 3}, "headings": {}})
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)
        audit.record_validator(state, reg, "HYG-01", confirmed(), False)
        self.assertEqual(audit.consolidate(state)["renamed"], {"HYG-01": "LEG-04"})


# ---------------------------------------------------------- AU-09 to AU-16

class DeliverableTest(EngineCase):
    LEGACY = ("# Upgrade roadmap — x\n\n## Milestone 1 — Assainir\n"
              "| ID | Finding | Severity | Size | Depends on | Spec | Status |\n|---|---|---|---|---|---|---|\n"
              "| LEG-01 | Secrets | critical | S | — | — | FAIT |\n"
              "| LEG-02 | Gitignore \\| bis | major | XS | — | — | EN_COURS |\n\n"
              "## Findings rejected at validation\n| ID | Finding | Reason |\n|---|---|---|\n"
              "| HYG-07 | Faux positif | fixture |\n")
    LAB = ("## Research questions retained (recommended order)\n### Q-02 — Seeds\n- **Question**: q ?\n"
           "- **Status**: TRANCHEE (journal 2026-10-01)\n")

    def test_au09_resume_reads_tables_and_sections_back_keeping_statuses(self):
        """AU-09: rows and sections of an existing deliverable go to the run registry,
        FAIT, EN_COURS and TRANCHEE preserved; their lines are kept as they are."""
        parsed = audit.parse_deliverable("legacy-audit", self.LEGACY)
        self.assertEqual([(e["id"], e["status"], e["section"], e["milestone"]) for e in parsed["entries"]],
                         [("LEG-01", "FAIT", "milestone", 1), ("LEG-02", "EN_COURS", "milestone", 1),
                          ("HYG-07", None, "rejected", None)])
        self.assertEqual(parsed["entries"][1]["title"], "Gitignore \\| bis")
        self.assertEqual(parsed["numbering"], {"LEG": 2})
        reg = registry.Registry(self.tmp / "r.jsonl")
        self.assertEqual(audit.register_resumed(parsed, reg), 3)
        self.assertEqual(reg.current("::faux positif"), "REJECTED")
        self.assertEqual(reg.current("::secrets"), "CONFIRMED")
        lab = audit.parse_deliverable("ai-lab-audit", self.LAB)
        self.assertEqual((lab["entries"][0]["status"], lab["numbering"]), ("TRANCHEE (journal 2026-10-01)", {"Q": 2}))
        reg = registry.Registry(self.tmp / "l.jsonl")
        audit.register_resumed(lab, reg)
        self.assertEqual(reg.last["::seeds"]["settled_by"], "journal 2026-10-01")
        doc = {"findings": [], "rejected": [], "resumed": parsed,
               "data": {"name": "x", "date": "2026-10-17", "factory": True, "milestones": []}}
        text = audit.render("legacy-audit", doc)
        for line in self.LEGACY.splitlines():
            if line.startswith("| LEG-") or line.startswith("| HYG-"):
                self.assertIn(line + "\n", text)
        self.assertIn("## Milestone 1 — Assainir\n", text)

    def test_au10_deliverable_status_sets_per_skill(self):
        """AU-10: A_FAIRE / EN_COURS / FAIT / REPORTE everywhere, TRANCHEE (journal <date>)
        for ai-lab-audit only; another status -> exit 2; set_status rewrites the cell or line."""
        for skill in audit.PROFILES:
            for status in ("A_FAIRE", "EN_COURS", "FAIT", "REPORTE"):
                audit.check_status(audit.profile(skill), status)
        audit.check_status(audit.profile("ai-lab-audit"), "TRANCHEE (journal 2026-10-01)")
        for skill in ("legacy-audit", "vibecode-audit"):
            with self.assertRaises(audit.AuditUsage):
                audit.check_status(audit.profile(skill), "TRANCHEE (journal 2026-10-01)")
        with self.assertRaises(audit.AuditUsage):
            audit.check_status(audit.profile("ai-lab-audit"), "DONE")
        with self.assertRaises(audit.AuditUsage):
            audit.parse_deliverable("legacy-audit", self.LEGACY.replace("FAIT", "FINI"))
        row = audit.parse_deliverable("legacy-audit", self.LEGACY)["entries"][0]
        self.assertEqual(audit.set_status("legacy-audit", row, "REPORTE")["line"],
                         "| LEG-01 | Secrets | critical | S | — | — | REPORTE |")
        sec = audit.parse_deliverable("ai-lab-audit", self.LAB.replace("TRANCHEE (journal 2026-10-01)", "A_FAIRE"))
        new = audit.set_status("ai-lab-audit", sec["entries"][0], "TRANCHEE (journal 2026-10-02)")
        self.assertEqual(new["lines"][-1], "- **Status**: TRANCHEE (journal 2026-10-02)")
        with self.assertRaises(audit.AuditUsage):
            audit.set_status("legacy-audit", row, "TRANCHEE (journal 2026-10-02)")

    def test_au11_forbidden_strings_and_allowed_paths(self):
        """AU-11 (FD-11, X5): a deliverable holding 'Statut : APPROUVEE' or
        'Statut : PRE-ENREGISTRE' is refused (exit 1), nothing written; only the
        deliverable, the skill's specs, .harness/runs/ and the registry are writable."""
        doc = {"findings": [], "rejected": [], "data": {"name": "Statut : APPROUVEE", "date": "d", "factory": True}}
        with self.assertRaises(audit.AuditStop):
            audit.render("legacy-audit", doc)
        dest = self.tmp / "ROADMAP-legacy.md"
        with self.assertRaises(audit.AuditStop):
            audit.write_text("legacy-audit", self.tmp, dest, "x\nStatut : PRE-ENREGISTRE\n")
        self.assertFalse(dest.exists())
        self.assertEqual(audit.write_text("legacy-audit", self.tmp, dest, "ok\n")["state"], "créé")
        for skill, ok, refused in (("legacy-audit", "specs/SPEC-leg-01-x.md", "specs/SPEC-lab-01-x.md"),
                                   ("ai-lab-audit", "AUDIT-lab.md", "ROADMAP-legacy.md"),
                                   ("vibecode-audit", ".harness/runs/x.json", "specs/SPEC-vib-01-x.md")):
            audit.check_writable(skill, self.tmp, self.tmp / ok)
            with self.assertRaises(audit.AuditUsage, msg=refused):
                audit.check_writable(skill, self.tmp, self.tmp / refused)
        with self.assertRaises(audit.AuditUsage):
            audit.check_writable("legacy-audit", self.tmp, self.tmp.parent / "ROADMAP-legacy.md")
        self.assertIn(".harness/runs/", audit.allowed_writes("vibecode-audit"))

    def test_au12_git_absent_no_git_command_afterwards(self):
        """AU-12: GIT_ABSENT detected once; afterwards no git command, no exclusion
        (the project publication runs no git)."""
        runner = helpers.FakeRunner().on(("git", "-C"), code=128, err="not a git repository")
        self.assertTrue(audit.probe_git(runner, self.tmp))
        self.assertFalse(audit.probe_git(helpers.FakeRunner(), self.tmp))
        state, reg = self.start("legacy-audit", "--categories hygiene")
        state["git_absent"] = True
        audit.record_auditor(state, reg, "hygiene", block({"findings": [legacy_item()]}), False)
        audit.record_validator(state, reg, "HYG-01", json.dumps({"verdict": "REJECTED", "reason": "r"}), False)
        calls = []
        real = registry.git_exclude
        registry.git_exclude = lambda *a, **k: calls.append(a) or []
        try:
            out = audit.publish(state, reg, "legacy-audit-20261017-000000.jsonl", True)
        finally:
            registry.git_exclude = real
        self.assertEqual((calls, out["excluded"], out["published"]), ([], None, 1))
        state["git_absent"] = False
        registry.git_exclude = lambda *a, **k: calls.append(a) or []
        try:
            audit.publish(state, reg, "legacy-audit-20261017-000000.jsonl", True)
        finally:
            registry.git_exclude = real
        self.assertEqual(len(calls), 1)

    def test_au13_report_counters_per_lens_or_family(self):
        """AU-13: raw, confirmed, rejected or deduplicated per unit; split and
        confirmed occurrences (vibecode), corrections and questions (ai-lab-audit)."""
        state, reg = self.start("vibecode-audit", "--axes maintenabilite")
        audit.record_auditor(state, reg, "maintenabilite", block({"findings": [
            vib_item("MNT-01", "p1", (("a.js", 1), ("b.js", 2), ("c.js", 3)), "major", "maintenabilite"),
            vib_item("MNT-02", "p2", (("a.js", 1), ("b.js", 2), ("c.js", 3)), "major", "maintenabilite"),
            vib_item("MNT-03", "p3", (("d.js", 1),), "minor", "maintenabilite")]}), False)
        split = {"verdict": "SPLIT", "reason": "two", "split_into": [
            {"pattern": "s1", "occurrences": ["a.js:1", "b.js:2"], "severity": "major", "difficulty": "S"},
            {"pattern": "s2", "occurrences": ["c.js:3"], "severity": "minor", "difficulty": "XS"}]}
        audit.record_validator(state, reg, "MNT-01", json.dumps(split), False)
        audit.record_validator(state, reg, "MNT-03", confirmed(), False)
        row = audit.counters(state)[0]
        self.assertEqual({k: row[k] for k in ("raw", "confirmed", "split", "rejected", "occurrences")},
                         {"raw": 3, "confirmed": 1, "split": 1, "rejected": 1, "occurrences": 4})
        self.assertEqual(list(state["findings"])[:2], ["MNT-01.1", "MNT-01.2"])
        state, reg = self.start("ai-lab-audit", "--lenses fidelite")
        audit.record_auditor(state, reg, "fidelite", block({"findings": [
            lab_item(), lab_item("FID-02", nature="question", line=8, title="q")]}), False)
        for fid in ("FID-01", "FID-02"):
            audit.record_validator(state, reg, fid, confirmed(), False)
        row = audit.counters(state)[0]
        self.assertEqual((row["corrections"], row["questions"], row["confirmed"]), (1, 1, 2))
        self.assertNotIn("split", audit.counters(self.start("legacy-audit")[0])[0])

    def test_au14_existing_spec_never_overwritten_whatever_its_slug(self):
        """AU-14 (SF-04): specs/SPEC-<prefix>-<nn>-*.md present -> kept as is, reported."""
        f = findings.adapt("legacy-auditor", legacy_item(size="XS"))
        sections = {"context": "c", "scope": "s", "criteria": ["c1"], "out_of_scope": "o", "risks": "r"}
        specs = self.tmp / "specs"
        specs.mkdir()
        mine = specs / "SPEC-leg-03-my-own.md"
        mine.write_text("my own spec\n", encoding="utf-8")
        out = audit.write_spec("legacy-audit", self.tmp, f, sections, 3, "other-slug")
        self.assertEqual(out, {"file": "specs/SPEC-leg-03-my-own.md", "state": "personnalisé-conservé"})
        self.assertEqual(mine.read_text(encoding="utf-8"), "my own spec\n")
        self.assertFalse((specs / "SPEC-leg-03-other-slug.md").exists())
        out = audit.write_spec("legacy-audit", self.tmp, f, sections, 4, "new")
        self.assertEqual(out["state"], "créé")
        self.assertTrue((specs / "SPEC-leg-04-new.md").read_text(encoding="utf-8").startswith("Status: PROPOSED"))
        for bad in ("Bad Slug", "", "x/../y"):
            with self.assertRaises(audit.AuditUsage):
                audit.write_spec("legacy-audit", self.tmp, f, sections, 5, bad)
        with self.assertRaises(audit.AuditUsage):
            audit.write_spec("vibecode-audit", self.tmp, f, sections, 1, "x")

    def test_au15_readjust_only_once(self):
        """AU-15: 'Readjust' replays the theme gate once, then disappears from the
        final options; a second readjustment -> exit 2."""
        state, _ = self.start("legacy-audit")
        label = audit.readjust_option("legacy-audit")
        self.assertIn(label, audit.final_options(state))
        self.assertEqual(audit.readjust(state)["gate"], "legacy-audit.assignment")
        self.assertNotIn(label, audit.final_options(state))
        self.assertEqual(len(audit.final_options(state)), 2)
        with self.assertRaises(audit.AuditUsage):
            audit.readjust(state)

    def test_au16_gate_calls_four_themes_per_call_and_max_calls(self):
        """AU-16: 4 themes at most per call; at most 3 calls (legacy-audit), 2
        (ai-lab-audit), 1 (vibecode-audit); beyond -> exit 2."""
        self.assertEqual(audit.gate_calls("legacy-audit", 9)["calls"], [[0, 1, 2, 3], [4, 5, 6, 7], [8]])
        self.assertEqual(audit.gate_calls("legacy-audit", 12)["max_calls"], 3)
        for skill, too_many in (("legacy-audit", 13), ("ai-lab-audit", 9), ("vibecode-audit", 5)):
            with self.assertRaises(audit.AuditUsage):
                audit.gate_calls(skill, too_many)
        self.assertEqual(len(audit.gate_calls("ai-lab-audit", 8)["calls"]), 2)
        self.assertEqual(audit.gate_calls("vibecode-audit", 3)["gate"], "vibecode-audit.order")
        with self.assertRaises(audit.AuditUsage):
            audit.gate_calls("legacy-audit", 0)
        res = helpers.harness(["audit", "themes", "--skill", "ai-lab-audit", "--count", "9"], self.tmp)
        self.assertEqual(res.returncode, 2)


# ------------------------------------------------------------------- replays

def replay(test, skill, workdir, fixture=None, git=None, keep=None, unified=False, cycle=False):
    """Plays the core calls of an audit through the CLI on a frozen fixture
    (tests/fixtures/audit/<skill>.json). Returns the outputs. `unified`: the /audit flow
    (start --unified, render of the set block, assemble into AUDIT.md)."""
    fx = fixture or json.loads((AUDIT_FIXTURES / ("%s.json" % skill)).read_text(encoding="utf-8"))
    repo = workdir / "repo"
    git = fx["git"] if git is None else git
    env = {}
    if git:
        helpers.init_repo(repo)
    else:
        repo.mkdir(parents=True, exist_ok=True)
        env["GIT_CEILING_DIRECTORIES"] = str(workdir)
    for name, text in (keep or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    if fx["project"]:
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "findings.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in fx["project"]), encoding="utf-8")
    h = lambda *a: helpers.harness(list(a), repo, env=env)  # noqa: E731
    out = {"repo": repo, "auditors": {}, "validators": {}}
    if cycle:
        check_ok(test, h("audit", "open", "--root", str(repo)))
    args = helpers.write_output(repo, "%s-arguments.txt" % skill, fx["arguments"])
    start = check_ok(test, h("audit", "start", "--skill", skill, "--arguments-file", args,
                             *(["--unified"] if unified else [])))
    test.assertFalse((repo / args).exists(), "transit file consumed")
    out["start"] = start
    run = start["run"]

    def call(kind, key, outputs, option):
        decisions = []
        for n, output in enumerate(outputs, 1):
            extra = ["--dead"]
            if output is not None:
                text = output if isinstance(output, str) else block(output)
                extra = ["--output", helpers.write_output(repo, "%s-%s-%d.md" % (kind, key, n), text)]
            decisions.append(check_ok(test, h("audit", kind, "--run", run, option, key, "--duration-ms", "10",
                                              "--tokens", "100", *extra)))
        return decisions

    for name, outputs in fx["auditors"].items():
        out["auditors"][name] = call("auditor", name, outputs, "--unit")
    for fid, outputs in fx["validators"].items():
        out["validators"][fid] = call("validator", fid, outputs, "--finding")
    out["next"] = check_ok(test, h("audit", "next", "--run", run))
    if out["next"]["step"] == "consolidate":
        out["consolidate"] = check_ok(test, h("audit", "consolidate", "--run", run))
    out["specs"] = []
    for spec in fx["specs"]:
        sections = helpers.write_output(repo, "sections-%d.json" % spec["number"], json.dumps(spec["sections"]))
        out["specs"].append(check_ok(test, h("audit", "spec", "--run", run, "--finding", spec["finding"],
                                             "--sections", sections, "--number", str(spec["number"]),
                                             "--slug", spec["slug"])))
    data = helpers.write_output(repo, "%s-data.json" % skill, json.dumps(fx["data"], ensure_ascii=False))
    out["render"] = check_ok(test, h("audit", "render", "--run", run, "--data-file", data))
    out["publish"] = check_ok(test, h("audit", "publish", "--run", run, "--deliverable"))
    out["run"] = repo / run
    if unified:
        out["assemble"] = check_ok(test, h("audit", "assemble", "--set-run", run, "--profiles", "p", "--lenses", "l",
                                           "--date", "2026-10-17"))
        out["deliverable"] = (repo / "AUDIT.md").read_text(encoding="utf-8")
    else:
        out["deliverable"] = (repo / audit.profile(skill).deliverable).read_text(encoding="utf-8")
    out["h"] = h
    return out


@unittest.skipUnless(helpers.HAS_FIXTURES, helpers.SKIP_FIXTURES)
class ReplayTest(helpers.TempDirCase):
    def play(self, skill):
        fx = json.loads((AUDIT_FIXTURES / ("%s.json" % skill)).read_text(encoding="utf-8"))
        return fx, replay(self, skill, self.tmp)

    def check_common(self, skill, fx, out):
        exp = fx["expected"]
        self.assertEqual(out["consolidate"]["renamed"], exp["renamed"])
        self.assertEqual(out["next"]["not_audited"], exp["not_audited"] + exp.get("surfaces", []))
        dismissed = {d["id"]: d["reason"] for decisions in out["auditors"].values() for decision in decisions
                     for d in decision.get("duplicates", [])}
        self.assertEqual(sorted(dismissed), sorted(exp["dismissed"]))
        for fid, previous in exp["dismissed"].items():
            if previous:
                self.assertEqual(dismissed[fid], "previous=%s" % previous)
        # A dismissed finding never reaches a validator (its validator call is refused).
        for fid in exp["dismissed"]:
            res = out["h"]("audit", "validator", "--run", str(out["run"].relative_to(out["repo"])),
                           "--finding", fid, "--dead")
            self.assertEqual(res.returncode, 2, fid)
        for fid in exp["unvalidated"]:
            self.assertEqual(out["validators"][fid][-1]["action"], "unvalidated")
        # The deliverable equals the versioned expected file, FD-11 finds nothing (CA-29).
        expected = (AUDIT_FIXTURES / ("%s.expected.md" % skill)).read_text(encoding="utf-8")
        self.assertEqual(out["deliverable"], expected)
        self.assertEqual(findings.check_forbidden(out["deliverable"]), [])
        res = out["h"]("finding", "forbidden", audit.profile(skill).deliverable,
                       *[s["file"] for s in out["specs"]])
        self.assertEqual(res.returncode, 0, res.stderr)
        # Every structural line of the BASE1 skeleton is in the rendered deliverable.
        skeleton = json.loads((AUDIT_FIXTURES / "skeletons.json").read_text(encoding="utf-8"))["skeletons"][skill]
        for line in skeleton:
            self.assertIn(line + "\n", out["deliverable"], line)
        # Journal: name and schema (CA-09); one line per auditor or validator call.
        self.assertTrue(journal.NAME_RE.match(out["run"].name), out["run"].name)
        self.assertEqual(journal.validate_file(out["run"]), [])
        calls = sum(len(v) for v in fx["auditors"].values()) + sum(len(v) for v in fx["validators"].values())
        self.assertEqual(len(journal.read(out["run"])), calls)
        return dismissed

    def test_au07_au08_au13_ca27_ca29_legacy_audit_replay(self):
        """CA-27, CA-29 (legacy-audit): dead family twice -> not audited, REJECTED
        project duplicate set aside without validator, dead validator -> UNVALIDATED,
        LEG renumbering, roadmap equal to the versioned expected file, specs FD-11 clean."""
        fx, out = self.play("legacy-audit")
        self.check_common("legacy-audit", fx, out)
        self.assertEqual([d["action"] for d in out["auditors"]["qualite"]], ["relaunch", "not-audited"])
        self.assertEqual([d["action"] for d in out["auditors"]["portabilite"]], ["relaunch", "validate"])
        rows = {r["unit"]: r for r in out["next"]["counters"]}
        self.assertEqual(rows["qualite"]["raw"], "failed")
        self.assertEqual((rows["filet"]["raw"], rows["filet"]["rejected"], rows["filet"]["unvalidated"]), (2, 1, 1))
        spec = (out["repo"] / "specs" / "SPEC-leg-01-purge-secrets.md").read_text(encoding="utf-8")
        self.assertTrue(spec.startswith("Status: PROPOSED\nBlocking questions: none\n\n# SPEC-leg-01 — Secret commité dans config.py\n"))
        self.assertIn("## Out of scope\nHistory rewriting ; Rotation", spec)
        self.assertEqual(out["publish"]["published"], 4)
        lines = (out["repo"] / ".claude" / "findings.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len({json.loads(x)["key"] for x in lines}))
        self.assertEqual(helpers.git(out["repo"], "status", "--porcelain", "--untracked-files=all").count(".claude/"), 0)

    def test_ca21_orderings_on_the_audit_fixtures(self):
        """CA-21 (TS-02, TS-03) on fixture: LEG order severity then size, VIB puts the
        critical of axis evolutivite before any major securite, regression_risk has no
        effect, an XL gives at least 2 specs <= L, 5 XS of one category and milestone -> 4 + 1."""
        fx, out = self.play("vibecode-audit")
        order = [f["id"] for f in out["consolidate"]["findings"]]
        severities = {f["id"]: (f["severity"], f["axis"]) for f in out["consolidate"]["findings"]}
        self.assertEqual(severities["VIB-02"], ("critical", "evolutivite"))
        majors_sec = [i for i in order if severities[i] == ("major", "securite")]
        self.assertTrue(majors_sec and order.index("VIB-02") < min(order.index(i) for i in majors_sec))
        self.assertEqual(out["consolidate"]["raised_criticals"], ["VIB-02"])
        shaken = json.loads(json.dumps(fx))
        rng = random.Random(3)
        for outputs in shaken["auditors"].values():
            for o in outputs:
                for item in (o or {}).get("findings", []) if isinstance(o, dict) else []:
                    item["regression_risk"] = rng.choice(["faible", "moyen", "eleve"])
        other = replay(self, "vibecode-audit", self.tmp / "shaken", shaken)
        self.assertEqual(other["consolidate"]["renamed"], fx["expected"]["renamed"])
        legacy = replay(self, "legacy-audit", self.tmp / "legacy")
        items = legacy["consolidate"]["findings"]
        ranks = [(findings.SEVERITIES.index(f["severity"]), tshirt.size_rank(f["size"])) for f in items]
        self.assertEqual(ranks, sorted(ranks))
        xl = dict(items[0], size="XL")
        self.assertGreaterEqual(len([s for s in tshirt.plan_specs([xl], "category-milestone")
                                     if s["max_size"] == "L"]), 2)
        five = [dict(items[1], id="X%d" % i, size="XS", milestone=1) for i in range(5)]
        self.assertEqual([len(s["findings"]) for s in tshirt.plan_specs(five, "category-milestone")], [4, 1])

    def test_ca27_ca29_ai_lab_audit_replay(self):
        """CA-27, CA-29 (ai-lab-audit): dead lens twice -> not audited, in-run duplicate
        set aside, unusable validator output relaunched, COR and Q numbering, AUDIT-lab.md
        equal to the expected file, the fuites spec carries both imposed criteria."""
        fx, out = self.play("ai-lab-audit")
        self.check_common("ai-lab-audit", fx, out)
        self.assertEqual([d["action"] for d in out["validators"]["VAL-01"]], ["relaunch", "confirmed"])
        rows = {r["unit"]: r for r in out["next"]["counters"]}
        self.assertEqual((rows["fuites"]["corrections"], rows["validite"]["questions"]), (1, 1))
        self.assertEqual(rows["bio"]["raw"], "failed")
        self.assertIn("- **Depends on**: COR-01".replace("- ", ""), out["deliverable"])
        spec = (out["repo"] / "specs" / "SPEC-lab-01-e2-meme-budget.md").read_text(encoding="utf-8")
        for criterion in findings.deliverable_texts()["leak_criteria"]:
            self.assertIn(criterion, spec)
        self.assertEqual(out["start"]["auditors"][0]["role"], "lab.math")

    def test_ca27_ca29_vibecode_audit_replay_git_absent(self):
        """CA-27, CA-29 (vibecode-audit, AU-12 GIT_ABSENT): dead family twice ->
        unauditable surface major, split parts, false occurrence removed from the count,
        UNVALIDATED marked 'not validated', no git exclusion; report equal to the expected file."""
        fx, out = self.play("vibecode-audit")
        self.check_common("vibecode-audit", fx, out)
        self.assertTrue(out["start"]["git_absent"])
        self.assertIsNone(out["start"]["exclude_added"])
        self.assertIsNone(out["publish"]["excluded"])
        self.assertEqual(out["auditors"]["injection"][-1]["action"], "surface")
        self.assertEqual(out["validators"]["MNT-01"][0]["parts"], ["MNT-01.1", "MNT-01.2"])
        self.assertIn("| 1 | VIB-01 | Les points d'entrée de modification ne vérifient pas l'appartenance "
                      "de la ressource | security | critical | 13 | M | high | yes | — | A_FAIRE |", out["deliverable"])
        self.assertIn("| not validated |", out["deliverable"])
        rows = {r["unit"]: r for r in out["next"]["counters"]}
        self.assertEqual((rows["maintenabilite"]["split"], rows["acces"]["occurrences"]), (1, 13))
        self.assertFalse((out["repo"] / ".git").exists())

    def test_ca27_headless_fallback_of_the_framing_gates(self):
        """CA-27: headless fallback of the framing (or order) gate: stop for
        legacy-audit and ai-lab-audit, default applied and reported for vibecode-audit."""
        for gid, code in (("legacy-audit.framing", 1), ("ai-lab-audit.framing", 1), ("vibecode-audit.order", 0)):
            d = check_ok(self, helpers.harness(["gate", "decide", "--id", gid], self.tmp))
            res = helpers.harness(["gate", "answer", "--run", d["run"], "--id", gid, "--headless"], self.tmp)
            self.assertEqual(res.returncode, code, (gid, res.stderr))
            if code == 0:
                self.assertEqual(json.loads(res.stdout)["answer"], "Severity first / Detail them")

    def resume_fixture(self, filet):
        fx = json.loads((AUDIT_FIXTURES / "legacy-audit.json").read_text(encoding="utf-8"))
        fx["project"] = []
        fx["auditors"] = {"hygiene": [{"findings": [dict(fx["auditors"]["hygiene"][0]["findings"][0])]}],
                          "portabilite": [{"findings": []}], "qualite": [{"findings": []}],
                          "filet": [{"findings": filet}]}
        fx["validators"] = {"HYG-01": [{"verdict": "CONFIRMED", "reason": "r", "size_correction": None}]}
        fx["data"]["findings"] = {"LEG-04": {"milestone": 1}}
        return fx

    def test_au09_ca30_resume_keeps_statuses_and_numbering(self):
        """CA-30 (AU-09, AU-08): with ROADMAP-legacy.md holding LEG-03 FAIT, LEG-03 stays
        FAIT, the numbering resumes at LEG-04, every resumed line is kept, and a finding
        already in the roadmap (same title) is set aside without a validator."""
        roadmap = (AUDIT_FIXTURES / "resume" / "roadmap.md").read_text(encoding="utf-8")
        again = {"id": "FIL-09", "category": "tests-absents", "severity": "major", "title": "Pas de .gitignore",
                 "location": {"file": "x.py", "line": 2}, "evidence": "e", "impact": "i", "remediation": "r",
                 "size": "XS", "depends_on": []}
        fx = self.resume_fixture([again])
        fx["specs"] = []
        out = replay(self, "legacy-audit", self.tmp, fx, keep={"ROADMAP-legacy.md": roadmap})
        self.assertEqual(out["start"]["resumed"], {"entries": 4, "numbering": {"LEG": 3}, "ids": {
            "milestone": ["LEG-01", "LEG-03"], "not_retained": ["LEG-02"], "rejected": ["HYG-07"]}})
        self.assertEqual(out["consolidate"]["renamed"], {"HYG-01": "LEG-04"})
        self.assertEqual([d["id"] for d in out["auditors"]["filet"][0]["duplicates"]], ["FIL-09"])
        for line in roadmap.splitlines():
            if line.startswith(("| LEG-", "| HYG-")):
                self.assertIn(line + "\n", out["deliverable"])
        self.assertIn("| LEG-03 | Pas de .gitignore | major | XS | — | [specs/SPEC-leg-03-gitignore.md]"
                      "(specs/SPEC-leg-03-gitignore.md) | FAIT |\n", out["deliverable"])
        self.assertIn("| LEG-04 | Secret commité dans config.py | critical | S | — | — | A_FAIRE |", out["deliverable"])
        self.assertLess(out["deliverable"].index("| LEG-03 |"), out["deliverable"].index("| LEG-04 |"))

    def test_au14_ca30_existing_spec_kept_byte_for_byte_through_the_cli(self):
        """CA-30, AU-14: an existing specs/SPEC-leg-03-*.md is never rewritten by
        'audit spec' (cmp), whatever the slug asked for; a new number is created."""
        roadmap = (AUDIT_FIXTURES / "resume" / "roadmap.md").read_text(encoding="utf-8")
        spec_bytes = (AUDIT_FIXTURES / "resume" / "SPEC-leg-03-gitignore.md").read_bytes()
        fx = self.resume_fixture([])
        sections = json.loads((AUDIT_FIXTURES / "legacy-audit.json").read_text(encoding="utf-8"))["specs"][0]["sections"]
        fx["specs"] = [{"finding": "LEG-04", "number": 3, "slug": "purge-secrets", "sections": sections},
                       {"finding": "LEG-04", "number": 4, "slug": "purge-secrets", "sections": sections}]
        out = replay(self, "legacy-audit", self.tmp, fx,
                     keep={"ROADMAP-legacy.md": roadmap,
                           "specs/SPEC-leg-03-gitignore.md": spec_bytes.decode("utf-8")})
        self.assertEqual(out["specs"][0], {"file": "specs/SPEC-leg-03-gitignore.md", "state": "personnalisé-conservé"})
        self.assertEqual((out["repo"] / "specs" / "SPEC-leg-03-gitignore.md").read_bytes(), spec_bytes)
        self.assertFalse((out["repo"] / "specs" / "SPEC-leg-03-purge-secrets.md").exists())
        self.assertEqual(out["specs"][1], {"file": "specs/SPEC-leg-04-purge-secrets.md", "state": "créé"})


if __name__ == "__main__":
    unittest.main()
