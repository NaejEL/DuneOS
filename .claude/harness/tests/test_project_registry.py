# Project registry .claude/findings.jsonl (RG-01 to RG-07, CA-16, CA-17) and
# its wiring in ship-pr (publication at the end of the loop, reading at start).

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from hcore import findings, registry, runs

GATED = (
    "import os, runpy, sys, time\n"
    "gate, harness = sys.argv[1], sys.argv[2]\n"
    "while not os.path.exists(gate):\n"
    "    time.sleep(0.001)\n"
    "sys.argv = [harness] + sys.argv[3:]\n"
    "runpy.run_path(harness, run_name='__main__')\n"
)
HOLD_FILE = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from hcore import runs\n"
    "with runs.locked_file(sys.argv[2]):\n"
    "    print('locked', flush=True)\n"
    "    time.sleep(float(sys.argv[3]))\n"
)
HOLD = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from hcore import runs\n"
    "with runs.locked(sys.argv[2]):\n"
    "    print('locked', flush=True)\n"
    "    time.sleep(float(sys.argv[3]))\n"
)


def check_ok(test, res):
    test.assertEqual(res.returncode, 0, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip().startswith("{") else res.stdout


def run_with(root, skill, events):
    """A run of `skill` whose run registry holds `events` (finding, verdict, extra)."""
    run = runs.create(root, skill, {})
    reg = registry.Registry(runs.registry_path(run))
    for f, verdict, extra in events:
        reg.add(f, verdict, **extra)
    return run, reg


class KeyTest(unittest.TestCase):
    def test_rg02_keys(self):
        """RG-02: D2 key; a pattern with occurrences -> '::<pattern>'; no file -> '::<title>'."""
        coded = {"title": "Off-by-one", "evidence": {"kind": "code", "file": "./src\\a.py"}}
        self.assertEqual(registry.project_key(coded), registry.finding_key("src/a.py", "off by one"))
        self.assertEqual(registry.project_key({"title": "Ownership NOT checked", "occurrences": [{"file": "a"}],
                                               "evidence": {"kind": "code", "file": "a"}}),
                         "::ownership not checked")
        self.assertEqual(registry.project_key({"title": "No CI", "evidence": {"kind": "structural", "file": None}}),
                         "::no ci")
        self.assertEqual(registry.project_key({"title": "x", "file": "b.py"}), "b.py::x")


class ProjectRegistryTest(helpers.TempDirCase):
    def test_rg01_rg03_one_line_per_finding_sorted_published_statuses(self):
        """RG-01, RG-03: one line per finding (last state), sorted by key; REJECTED,
        FIXED, NOT_FIXED, SETTLED published; CONFIRMED only with its deliverable;
        neither DUPLICATE nor UNVALIDATED; idempotent, byte for byte."""
        events = [(helpers.finding(file="z.py", title="Zed"), "REJECTED", {"reason": "no"}),
                  (helpers.finding(file="a.py", title="Alpha"), "CONFIRMED", {}),
                  (helpers.finding(file="a.py", title="Alpha"), "FIXED", {"commit": "abc"}),
                  (helpers.finding(file="m.py", title="Mid"), "NOT_FIXED", {"reason": "scope"}),
                  (helpers.finding(file="d.py", title="Dup"), "DUPLICATE", {"of": "x"}),
                  (helpers.finding(file="u.py", title="Unv"), "UNVALIDATED", {}),
                  (helpers.finding(file="c.py", title="Conf"), "CONFIRMED", {}),
                  (helpers.finding(file="s.py", title="Settled"), "SETTLED", {"settled_by": "journal 2026-09-01"})]
        run, reg = run_with(self.tmp, "legacy-audit", events)
        project = registry.ProjectRegistry(self.tmp)
        out = project.publish(reg.last.values(), "legacy-audit", run.name)
        self.assertEqual(out["published"], 4)
        path = self.tmp / ".claude" / "findings.jsonl"
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["key"] for x in lines], ["a.py::alpha", "m.py::mid", "s.py::settled", "z.py::zed"])
        self.assertEqual([x["status"] for x in lines], ["FIXED", "NOT_FIXED", "SETTLED", "REJECTED"])
        self.assertEqual(lines[0]["fix_commit"], "abc")
        self.assertEqual(lines[2]["settled_by"], "journal 2026-09-01")
        before = path.read_bytes(), path.stat().st_mtime_ns
        again = project.publish(reg.last.values(), "legacy-audit", run.name)
        self.assertFalse(again["changed"])
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        with_deliverable = project.publish(reg.last.values(), "legacy-audit", run.name, deliverable_written=True)
        self.assertEqual(with_deliverable["published"], 5)
        self.assertIn("c.py::conf", project.read())
        self.assertEqual(len(project.read()), 5)

    def test_rg04_rejected_and_settled_dismiss_fixed_is_history(self):
        """RG-04: REJECTED and SETTLED set a finding aside, noted previous=<status>@<source>;
        FIXED and NOT_FIXED are history only."""
        run, reg = run_with(self.tmp, "ship-pr", [
            (helpers.finding(file="a.py", title="Rej"), "REJECTED", {}),
            (helpers.finding(file="a.py", title="Fix"), "CONFIRMED", {}),
            (helpers.finding(file="a.py", title="Fix"), "FIXED", {"commit": "c1"})])
        project = registry.ProjectRegistry(self.tmp)
        project.publish(reg.last.values(), "ship-pr", run.name)
        self.assertEqual(project.lookup("a.py::rej"), {"key": "a.py::rej", "dismiss": True,
                                                       "previous": "REJECTED@ship-pr"})
        self.assertEqual(project.lookup("a.py::fix"), {"key": "a.py::fix", "dismiss": False,
                                                       "previous": "FIXED@ship-pr"})
        self.assertEqual(project.lookup("a.py::new")["previous"], None)

    def test_rg05_git_exclusion_idempotent_and_nothing_without_git(self):
        """RG-05: .git/info/exclude completed once; a directory without git: nothing."""
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(registry.git_exclude(plain, registry.PROJECT_EXCLUDE))
        self.assertFalse((plain / ".git").exists())
        repo = self.tmp / "repo"
        helpers.init_repo(repo)
        self.assertEqual(registry.git_exclude(repo, registry.PROJECT_EXCLUDE), list(registry.PROJECT_EXCLUDE))
        self.assertEqual(registry.git_exclude(repo, registry.PROJECT_EXCLUDE), [])
        text = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertEqual(text.count(".claude/findings.jsonl\n"), 1)
        self.assertIn(".claude/findings.jsonl.*.tmp\n", text)

    def test_rg05_exclusion_written_under_lock_and_atomically(self):
        """RG-05, iteration 2: .git/info/exclude is rewritten under its own lock
        (a concurrent writer is waited for) and atomically (os.replace of a
        temporary file); CRLF kept."""
        repo = self.tmp / "repo"
        helpers.init_repo(repo)
        exclude = repo / ".git" / "info" / "exclude"
        exclude.write_bytes(b"# mine\r\n")
        lock = exclude.with_name("exclude.hcore-lock")
        proc = subprocess.Popen([sys.executable, "-B", "-c", HOLD_FILE, str(helpers.CORE), str(lock), "30"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.communicate()))
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        with mock.patch.object(runs, "LOCK_TIMEOUT", 0.3):
            with self.assertRaises(runs.LockTimeout):
                registry.git_exclude(repo, registry.PROJECT_EXCLUDE)
        self.assertEqual(exclude.read_bytes(), b"# mine\r\n")
        proc.kill()
        proc.communicate()
        with mock.patch.object(runs, "write_atomic", wraps=runs.write_atomic) as atomic:
            registry.git_exclude(repo, registry.PROJECT_EXCLUDE)
        self.assertEqual(atomic.call_count, 1)
        self.assertEqual(exclude.read_bytes(), b"# mine\r\n.claude/findings.jsonl\r\n.claude/findings.lock\r\n"
                                               b".claude/findings.jsonl.*.tmp\r\n")

    def test_rg06_publication_waits_for_the_lock(self):
        """RG-06: the publication holds the runs.locked lock of the registry file."""
        path = self.tmp / ".claude" / "findings.jsonl"
        path.parent.mkdir(parents=True)
        proc = subprocess.Popen([sys.executable, "-B", "-c", HOLD, str(helpers.CORE), str(path), "30"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.communicate()))
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        self.assertEqual(runs.lock_path(path), self.tmp / ".claude" / "findings.lock")
        run, reg = run_with(self.tmp, "ship-pr", [(helpers.finding(), "REJECTED", {})])
        with mock.patch.object(runs, "LOCK_TIMEOUT", 0.3):
            with self.assertRaises(runs.LockTimeout):
                registry.ProjectRegistry(self.tmp).publish(reg.last.values(), "ship-pr", run.name)
        self.assertFalse(path.exists())

    def test_rg07_existing_d2_run_registry_published_without_migration(self):
        """RG-07: a D2 run registry written before this lot is read as is."""
        run = runs.create(self.tmp, "ship-pr", {})
        old = [{"key": "src/a.py::bug", "file": "src/a.py", "line": 3, "title": "Bug", "verdict": "CONFIRMED",
                "round": 1, "id": "R1-F1", "reason": "real"},
               {"key": "src/a.py::bug", "file": "src/a.py", "line": 3, "title": "Bug", "verdict": "FIXED",
                "round": 1, "id": "R1-F1", "commit": "abc"}]
        runs.registry_path(run).write_text("".join(json.dumps(x) + "\n" for x in old), encoding="utf-8")
        out = check_ok(self, helpers.harness(["registry", "publish", "--run", str(run)], self.tmp))
        self.assertEqual(out["published"], 1)
        entry = registry.ProjectRegistry(self.tmp).read()["src/a.py::bug"]
        self.assertEqual((entry["status"], entry["fix_commit"], entry["source"]), ("FIXED", "abc", "ship-pr"))


class EndRoundPublicationTest(helpers.TempDirCase):
    def test_rg03_end_round_publishes_before_saving_the_final_state(self):
        """RG-03, iteration 2: 'end-round' publishes to the project registry BEFORE
        saving the exit: a lock timeout (exit 1) leaves the round open, and the same
        'end-round' run again publishes then exits."""
        import harness
        from hcore import shippr
        run = runs.create(self.tmp, "ship-pr", shippr.new_state("main", 3, ["logic"]))
        state = runs.load_state(run)
        reg = registry.Registry(runs.registry_path(run))
        out = helpers.reviewer_block([helpers.finding(title="Fake")])
        decision, _ = shippr.record_reviewer(state, reg, "logic", out, False)
        shippr.record_validator(state, reg, decision["to_validate"][0]["id"], helpers.validator_block("REJECTED"),
                                False)
        runs.save_state(run, state)
        real = registry.ProjectRegistry.publish

        def busy(*args, **kwargs):
            raise runs.LockTimeout("findings.jsonl locked")
        with mock.patch.object(registry.ProjectRegistry, "publish", busy):
            with mock.patch.object(sys, "stderr", new=__import__("io").StringIO()):
                self.assertEqual(harness.main(["ship-pr", "end-round", "--run", str(run)]), 1)
        self.assertIsNone(runs.load_state(run)["exit"])
        self.assertFalse((self.tmp / ".claude" / "findings.jsonl").exists())
        with mock.patch.object(registry.ProjectRegistry, "publish", real):
            with mock.patch.object(sys, "stdout", new=__import__("io").StringIO()) as stdout:
                self.assertEqual(harness.main(["ship-pr", "end-round", "--run", str(run)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["exit"], "exhaustion")
        self.assertEqual(runs.load_state(run)["exit"], "exhaustion")
        self.assertEqual(registry.ProjectRegistry(self.tmp).read()["src/a.py::fake"]["status"], "REJECTED")


class ConcurrentPublicationTest(helpers.TempDirCase):
    def test_ca17_rg06_concurrent_publications_lose_no_line(self):
        """CA-17, RG-06: 4 concurrent publications to findings.jsonl, over 8 trials:
        no lost line, no PermissionError."""
        for trial in range(8):
            root = self.tmp / ("t%d" % trial)
            root.mkdir()
            calls = []
            for n in range(4):
                run, _ = run_with(root, "ship-pr", [
                    (helpers.finding(file="f%d.py" % n, title="T%d-%d" % (n, k)), "REJECTED", {}) for k in range(3)])
                calls.append(["registry", "publish", "--run", str(run)])
            gate = root / "gate"
            procs = [subprocess.Popen([sys.executable, "-B", "-c", GATED, str(gate), str(helpers.HARNESS)] + args,
                                      cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      encoding="utf-8", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                     for args in calls]
            time.sleep(0.3)
            gate.write_text("go", encoding="utf-8")
            for proc in procs:
                out, err = proc.communicate(timeout=180)
                self.assertEqual(proc.returncode, 0, err)
                self.assertNotIn("PermissionError", err)
            keys = registry.ProjectRegistry(root).read()
            self.assertEqual(len(keys), 12, trial)
            text = (root / ".claude" / "findings.jsonl").read_text(encoding="utf-8")
            self.assertEqual(len(text.splitlines()), 12)


class ShipPrProjectReplayTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        bare = self.tmp / "github.com" / "acme" / "toy.git"
        bare.mkdir(parents=True)
        helpers.git(bare, "init", "-q", "--bare", "-b", "main")
        self.work = self.tmp / "work"
        helpers.init_repo(self.work)
        (self.work / "src").mkdir()
        (self.work / "src" / "sum.js").write_text("function sum(v) {\n    return 0;\n}\n", encoding="utf-8")
        helpers.git(self.work, "add", "-A")
        helpers.git(self.work, "commit", "-q", "-m", "init")
        helpers.git(self.work, "remote", "add", "origin", bare.as_posix())
        helpers.git(self.work, "push", "-q", "-u", "origin", "main")
        helpers.git(self.work, "checkout", "-q", "-b", "feature")
        (self.work / "src" / "sum.js").write_text(
            "function sum(v) {\n    return v.reduce((a, b) => a + b);\n}\n", encoding="utf-8")
        helpers.git(self.work, "commit", "-q", "-am", "sum")
        self.env = {"HCORE_GH": json.dumps([sys.executable, "-B", str(helpers.TESTS / "fake_gh.py")]),
                    "HCORE_FAKE_GH_STATE": str(self.tmp / "gh-state.json"), "HCORE_TEST_MODE": "1"}

    def h(self, *args):
        return helpers.harness(list(args), self.work, env=self.env)

    def review(self, run, findings_, theme="logic"):
        out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(findings_))
        return check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", theme,
                                     "--duration-ms", "9", "--output", out))

    def validate(self, run, fid, verdict):
        out = helpers.write_output(self.work, "validator.md", helpers.validator_block(verdict))
        return check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", fid,
                                     "--duration-ms", "3", "--output", out))

    def test_ca16_rg03_rg04_rejected_in_run_1_set_aside_in_run_2_and_in_legacy(self):
        """CA-16, RG-03, RG-04: a finding REJECTED in ship-pr run 1 is set aside before
        any validator in run 2 and in a legacy-audit check (previous=REJECTED@ship-pr);
        a FIXED finding of the same key is not; one line per key, a second publication
        is byte for byte identical, and git status never lists .claude/."""
        fake = helpers.finding(file="src/sum.js", line=1, title="Parameter name too short", severity="minor")
        real = helpers.finding(file="src/sum.js", line=2, title="reduce without initial value")
        start = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--rounds", "1", "--themes", "logic"))
        run = start["run"]
        self.assertEqual(start["project_findings"], 0)
        # RG-05, iteration 2: excluded from the preflight on, before any publication.
        exclude = (self.work / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
        for line in (".harness/runs/",) + registry.PROJECT_EXCLUDE:
            self.assertEqual(exclude.count(line), 1, line)
        check_ok(self, self.h("ship-pr", "sync", "--run", run))
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title", "Sum", "--body-file", body))
        f_fake, f_real = [x["id"] for x in self.review(run, [fake, real])["to_validate"]]
        self.assertEqual(self.validate(run, f_fake, "REJECTED")["action"], "drop")
        self.assertEqual(self.validate(run, f_real, "CONFIRMED")["action"], "post")
        check_ok(self, self.h("ship-pr", "comment", "--run", run, "--finding", f_real))
        (self.work / "src" / "sum.js").write_text(
            "function sum(v) {\n    return v.reduce((a, b) => a + b, 0);\n}\n", encoding="utf-8")
        check_ok(self, self.h("ship-pr", "commit", "--run", run, "--summary", "initial value", "--fixed", f_real))
        check_ok(self, self.h("ship-pr", "threads", "--run", run))
        end = check_ok(self, self.h("ship-pr", "end-round", "--run", run))
        self.assertEqual(end["exit"], "cap")
        self.assertEqual((end["project_registry"]["published"], end["project_registry"]["changed"]), (2, True))
        path = self.work / ".claude" / "findings.jsonl"
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted((x["key"], x["status"]) for x in lines),
                         [("src/sum.js::parameter name too short", "REJECTED"),
                          ("src/sum.js::reduce without initial value", "FIXED")])
        first = path.read_bytes()
        again = check_ok(self, self.h("registry", "publish", "--run", run))
        self.assertFalse(again["changed"])
        self.assertEqual(path.read_bytes(), first)
        self.assertNotIn(".claude", helpers.git(self.work, "status", "--porcelain", "--untracked-files=all"))

        # Run 2: same file and same title.
        run2 = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--themes", "logic"))
        self.assertEqual(run2["project_findings"], 2)
        r = self.review(run2["run"], [fake, real])
        self.assertEqual([(d["title"], d["previous"]) for d in r["duplicates"]],
                         [("Parameter name too short", "REJECTED@ship-pr")])
        self.assertEqual([(t["title"], t["history"]) for t in r["to_validate"]],
                         [("reduce without initial value", "FIXED@ship-pr")])

        # legacy-audit replay: its findings are checked against the project registry.
        legacy = [findings.adapt("legacy-auditor", {
            "id": "QUAL-01", "category": "naming", "severity": "minor", "title": "Parameter name too short",
            "location": {"file": "src/sum.js", "line": 1}, "evidence": "function sum(v)", "impact": "i",
            "remediation": "r", "size": "XS", "depends_on": []}),
            findings.adapt("legacy-auditor", {
                "id": "QUAL-02", "category": "bug", "severity": "major", "title": "Reduce without initial value",
                "location": {"file": "src/sum.js", "line": 2}, "evidence": "reduce", "impact": "i",
                "remediation": "r", "size": "XS", "depends_on": []})]
        lpath = self.tmp / "legacy.json"
        lpath.write_text(json.dumps(legacy), encoding="utf-8")
        checked = check_ok(self, self.h("registry", "check", "--file", str(lpath)))
        self.assertEqual(checked["dismissed"], [{"id": "QUAL-01", "key": "src/sum.js::parameter name too short",
                                                 "previous": "REJECTED@ship-pr"}])
        self.assertEqual([x["id"] for x in checked["history"]], ["QUAL-02"])
        self.assertNotIn(".claude", helpers.git(self.work, "status", "--porcelain", "--untracked-files=all"))


if __name__ == "__main__":
    unittest.main()
