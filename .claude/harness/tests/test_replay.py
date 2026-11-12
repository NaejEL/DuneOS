# Scripted replays (D4): the sequence of core calls prescribed by each
# SKILL.md, played through the CLI in a temporary directory.
#
# - CA15: factory-run (FR-01 to FR-04): Builder, Verifier CHANGES_REQUESTED,
#   Builder, dead Verifier, Verifier APPROVED.
# - CA16: ship-pr (SP-01 to SP-19) with a simulated gh and a throwaway git
#   repository: themes, validator, exit by exhaustion, REJECTED resubmitted
#   in round 2.

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
from hcore import fusion, ghflow, journal, registry

APPROVED_SPEC = "# SPEC\n\nStatut : APPROUVEE\n\nBody.\n"


def check_ok(test, res):
    test.assertEqual(res.returncode, 0, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip().startswith("{") else res.stdout


class FactoryRunReplayTest(helpers.TempDirCase):
    def test_ca15_factory_run_sequence(self):
        """FR-01 FR-02 FR-03 FR-04: full replay and conforming journal."""
        project = self.tmp / "toy"
        if helpers.HAS_FIXTURES:
            shutil.copytree(str(helpers.FIXTURES / "toy-project"), str(project))
            spec = "specs/SPEC-somme.md"
        else:
            (project / "specs").mkdir(parents=True)
            (project / "specs" / "SPEC-x.md").write_text(APPROVED_SPEC, encoding="utf-8")
            spec = "specs/SPEC-x.md"
        h = lambda *a, **kw: helpers.harness(list(a), project, **kw)  # noqa: E731

        # $ARGUMENTS is written to a file by the Write tool, never passed to the shell.
        args_file = project / ".harness" / "runs" / "factory-run-arguments.txt"
        args_file.parent.mkdir(parents=True)
        args_file.write_text(spec + "\n", encoding="utf-8")
        start = check_ok(self, h("factory-run", "start", "--arguments-file", str(args_file)))
        self.assertEqual(start["mode"], "build")
        run = start["run"]
        common = ["factory-run", "record", "--run", run, "--model", "inherit"]

        d = check_ok(self, h(*common, "--agent", "factory-builder", "--duration-ms", "1500", "--tokens", "900"))
        self.assertEqual(d["action"], "verify")
        # The Verifier output is written by the Write tool under .harness/runs/, never through the shell.
        cr = helpers.verifier_block("CHANGES_REQUESTED", False,
                                    [{"file": "src/sum.js", "severity": "major", "description": helpers.TRAP}])
        out = helpers.write_output(project, "verifier-1.md", cr)
        d = check_ok(self, h(*common, "--agent", "factory-verifier", "--duration-ms", "800", "--output", out))
        self.assertEqual((d["action"], d["iteration"]), ("fix", 2))
        self.assertEqual(d["issues"][0]["description"], helpers.TRAP)
        d = check_ok(self, h(*common, "--agent", "factory-builder", "--duration-ms", "1200"))
        self.assertEqual(d["action"], "verify")
        d = check_ok(self, h(*common, "--agent", "factory-verifier", "--duration-ms", "50", "--dead"))
        self.assertEqual(d["action"], "relaunch")
        check_ok(self, h("begin", "--run", run, "--slot", "factory-verifier"))
        out = helpers.write_output(project, "verifier-2.md", helpers.verifier_block("APPROVED", True))
        d = check_ok(self, h(*common, "--agent", "factory-verifier", "--output", out))
        self.assertEqual(d["action"], "success")

        path = project / run
        self.assertRegex(path.name, journal.NAME_RE)
        self.assertEqual(path.parent, project / ".harness" / "runs")
        check_ok(self, h("journal", "validate", run))
        lines = [x for x in journal.read(path) if "event" not in x]
        agents = [x["agent"] for x in lines]
        self.assertEqual(agents, ["factory-builder", "factory-verifier", "factory-builder",
                                  "factory-verifier", "factory-verifier"])
        self.assertTrue(all(x["iteration"] <= 3 for x in lines))
        dead, alive = lines[3], lines[4]
        self.assertEqual(dead["verdict"], "DEAD")
        self.assertEqual(dead["iteration"], alive["iteration"])
        self.assertEqual(alive["verdict"], "APPROVED")
        self.assertEqual(lines[0]["tokens"], 900)
        self.assertIsNone(lines[2]["tokens"])
        self.assertEqual(lines[1]["findings"]["produced"], 1)
        res = h(*common, "--agent", "factory-builder", "--duration-ms", "1")
        self.assertNotEqual(res.returncode, 0)

    def test_fr01_free_text_need_never_goes_through_shell(self):
        """FR-01: a need containing quotes, $ and backtick is read from a file;
        it leads to the Plan phase without error."""
        need = 'Export "everything" as CSV: `date` and $(whoami); then ${HOME}\nline 2'
        args_file = self.tmp / "arguments.txt"
        args_file.write_text(need, encoding="utf-8")
        start = check_ok(self, helpers.harness(["factory-run", "start", "--arguments-file", str(args_file)],
                                               self.tmp))
        self.assertEqual((start["mode"], start["spec"]), ("plan", None))
        bare = check_ok(self, helpers.harness(["factory-run", "start"], self.tmp))
        self.assertEqual(bare["mode"], "plan")


class ShipPrReplayTest(helpers.TempDirCase):
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

    def h(self, *args, stdin=None):
        return helpers.harness(list(args), self.work, stdin=stdin, env=self.env)

    def gh_state(self):
        return json.loads((self.tmp / "gh-state.json").read_text(encoding="utf-8"))

    def test_ca16_ship_pr_sequence(self):
        """SP-01 to SP-19: full replay, journal, registry, merge."""
        bad = self.h("ship-pr", "start", "--target", "main", "--rounds", "0")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("--rounds", bad.stderr)
        on_target = helpers.harness(["ship-pr", "start", "--target", "feature"], self.work, env=self.env)
        self.assertEqual(on_target.returncode, 1)
        self.assertIn("you are already on feature", on_target.stderr)

        start = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--themes", "logic;regressions;consistency"))
        run, themes = start["run"], start["themes"]
        self.assertEqual((start["rounds"], start["dirty"], start["owner_repo"]), (3, False, "acme/toy"))
        self.assertTrue(start["exclude_added"])
        # HK-05: start leaves the run `running`, so that `activate` makes the scope hooks apply to it.
        state = json.loads((self.work / (run[: -len(".jsonl")] + ".state.json")).read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "running")
        self.assertTrue(check_ok(self, self.h("ship-pr", "sync", "--run", run))["pushed"])
        material = check_ok(self, self.h("ship-pr", "pr", "--run", run))
        self.assertIsNone(material["number"])
        self.assertIn("sum", material["log"])
        # PR body and agent outputs: written by the Write tool under .harness/runs/.
        body = helpers.write_output(self.work, "pr-body.md", helpers.TRAP)
        pr = check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title", "Sum", "--body-file", body))
        self.assertEqual((pr["number"], pr["created"], pr["conflicting"]), (7, True, False))
        self.assertEqual(self.gh_state()["prs"][-1]["body"], helpers.TRAP)
        self.assertIn("reduce", check_ok(self, self.h("ship-pr", "diff", "--run", run)))

        def review(theme, findings, *extra):
            out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(findings))
            return check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", theme, "--model", "haiku",
                                         "--duration-ms", "900", "--tokens", "1200", "--output", out, *extra))

        def validate(fid, verdict):
            out = helpers.write_output(self.work, "validator.md", helpers.validator_block(verdict))
            return check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", fid, "--model", "haiku",
                                         "--duration-ms", "300", "--output", out))

        real = helpers.finding(file="src/sum.js", line=2, title="reduce without initial value")
        real["description"] = helpers.TRAP
        fake = helpers.finding(file="src/sum.js", line=1, title="Parameter name too short", severity="minor")
        # Round 1
        r1 = review("logic", [real])["to_validate"][0]
        self.assertEqual(r1["description"], helpers.TRAP)
        f1 = r1["id"]
        dead = check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", "regressions",
                                     "--model", "haiku", "--duration-ms", "10", "--dead"))
        self.assertEqual(dead["action"], "relaunch")
        f2 = review("regressions", [fake])["to_validate"][0]["id"]
        self.assertEqual(review("consistency", [])["action"], "none")
        self.assertEqual(validate(f1, "CONFIRMED")["action"], "post")
        # Logging defect (neither duration nor 'begin', invalid tokens, empty model):
        # reported, a fallback value is logged, never blocking; the verdict is taken into account.
        out = helpers.write_output(self.work, "validator.md", helpers.validator_block("REJECTED"))
        dropped = check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", f2, "--model", " ",
                                        "--tokens", "lots", "--output", out))
        self.assertEqual(dropped["action"], "drop")
        self.assertEqual(len(dropped["journal_warnings"]), 3)
        self.assertIn("overrides", dropped["hcore_test_mode"])
        posted = check_ok(self, self.h("ship-pr", "comment", "--run", run, "--finding", f1))
        self.assertEqual(posted["mode"], "inline")
        (self.work / "src" / "sum.js").write_text(
            "function sum(v) {\n    return v.reduce((a, b) => a + b, 0);\n}\n", encoding="utf-8")
        # The fix also touches a file never flagged: round 2 must review it.
        (self.work / "src" / "sum.test.js").write_text("assert(sum([]) === 0);\n", encoding="utf-8")
        commit = check_ok(self, self.h("ship-pr", "commit", "--run", run, "--summary", "initial value",
                                       "--fixed", f1))
        self.assertEqual(helpers.git(self.work, "log", "-1", "--format=%s").strip(),
                         "fix(review): round 1 fixes — initial value")
        threads = check_ok(self, self.h("ship-pr", "threads", "--run", run))
        self.assertEqual(threads["resolved"], [f1])
        self.assertIn("Fixed in %s." % commit["commit"], self.gh_state()["replies"][0]["body"])
        end1 = check_ok(self, self.h("ship-pr", "end-round", "--run", run))
        self.assertEqual(end1["next_round"], 2)
        self.assertEqual((end1["counts"]["raw"], end1["counts"]["confirmed"], end1["counts"]["rejected"]), (2, 1, 1))
        # Round 2 (SP-15): the fix commit is reviewed, unflagged files included.
        diff2 = check_ok(self, self.h("ship-pr", "diff", "--run", run))
        self.assertIn("src/sum.test.js", diff2)
        self.assertIn("a + b, 0);", diff2)
        # The REJECTED finding is resubmitted
        again = review("logic", [fake])
        self.assertEqual(again["to_validate"], [])
        self.assertEqual(again["duplicates"][0]["previous"], "REJECTED")
        review("regressions", [])
        review("consistency", [])
        end2 = check_ok(self, self.h("ship-pr", "end-round", "--run", run))
        self.assertEqual(end2["exit"], "exhaustion")
        summary = check_ok(self, self.h("ship-pr", "merge", "--run", run, "--check"))
        self.assertEqual(len(summary["rounds"]), 2)
        merged = check_ok(self, self.h("ship-pr", "merge", "--run", run))
        self.assertTrue(merged["merged"])
        self.assertTrue(self.gh_state()["merged"])
        self.assertEqual(helpers.git(self.work, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")

        path = self.work / run
        self.assertRegex(path.name, journal.NAME_RE)
        check_ok(self, self.h("journal", "validate", run))
        lines = [x for x in journal.read(path) if "event" not in x]
        self.assertTrue(all(x["iteration"] <= 3 for x in lines))
        reviewer_themes = {x["theme"] for x in lines if x["agent"] == "reviewer"}
        self.assertEqual(reviewer_themes, set(themes))
        validators = [x for x in lines if x["agent"] == "validator"]
        self.assertEqual([x["iteration"] for x in validators], [1, 1])
        self.assertEqual((validators[1]["duration_ms"], validators[1]["tokens"]), (None, None))
        self.assertEqual(validators[1]["model"], journal.FALLBACK_TEXT)
        reg_path = path.with_name(path.name[: -len(".jsonl")] + ".registry.jsonl")
        reg = [json.loads(x) for x in reg_path.read_text(encoding="utf-8").splitlines()]
        rejected = [x for x in reg if x["verdict"] == "REJECTED"]
        self.assertEqual(len(rejected), 1)
        self.assertFalse([x for x in reg if x.get("round") == 2])
        self.assertEqual(helpers.git(self.work, "status", "--porcelain", "--untracked-files=all"), "")


    def test_untrusted_text_travels_by_file_drop_and_trailer(self):
        """BACKLOG section 7: --title-file, --summary-file, --not-fixed-file carry
        backticks and $() unchanged; --not-fixed given twice is refused; drop of
        a semantic duplicate; --trailer after a blank line."""
        start = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--themes", "logic;regressions"))
        run = start["run"]
        check_ok(self, self.h("ship-pr", "sync", "--run", run))
        title = "Sum: `date` and $(whoami) for O'Brien"
        title_file = helpers.write_output(self.work, "pr-title.txt", title + "\n")
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        self.assertEqual(self.h("ship-pr", "pr", "--run", run, "--title", "x", "--title-file", title_file,
                                "--body-file", body).returncode, 2)
        # Transit files are consumed even by a refused call: written again.
        title_file = helpers.write_output(self.work, "pr-title.txt", title + "\n")
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title-file", title_file, "--body-file", body))
        self.assertEqual(self.gh_state()["prs"][-1]["title"], title)

        def review(theme, findings):
            out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(findings))
            return check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", theme, "--model", "haiku",
                                         "--duration-ms", "9", "--output", out))["to_validate"]

        a, b = [f["id"] for f in review("logic", [helpers.finding(file="src/sum.js", line=2, title="Empty crash"),
                                                  helpers.finding(file="src/sum.js", line=1, title="Naming")])]
        dup = review("regressions", [helpers.finding(file="src/sum.js", line=2, title="reduce on []")])[0]["id"]
        reason = helpers.write_output(self.work, "drop.txt", "same crash as `%s`\n" % a)
        self.assertEqual(self.h("ship-pr", "drop", "--run", run, "--finding", dup, "--of", "R7-F7").returncode, 2)
        dropped = check_ok(self, self.h("ship-pr", "drop", "--run", run, "--finding", dup, "--of", a,
                                        "--reason-file", reason))
        self.assertEqual((dropped["action"], dropped["reason"]), ("dropped", "same crash as `%s`" % a))
        for fid in (a, b):
            out = helpers.write_output(self.work, "validator.md", helpers.validator_block("CONFIRMED"))
            check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", fid, "--model", "haiku",
                                  "--duration-ms", "3", "--output", out))
        (self.work / "src" / "sum.js").write_text(
            "function sum(v) {\n    return v.reduce((a, b) => a + b, 0);\n}\n", encoding="utf-8")
        head = helpers.git(self.work, "rev-parse", "HEAD").strip()
        twice = self.h("ship-pr", "commit", "--run", run, "--summary", "s", "--fixed", a,
                       "--not-fixed", "%s:r1" % b, "--not-fixed", "%s:r2" % b)
        self.assertEqual(twice.returncode, 2)
        self.assertIn("twice", twice.stderr)
        nf_file = helpers.write_output(self.work, "not-fixed.json", json.dumps({b: helpers.TRAP}))
        twice = self.h("ship-pr", "commit", "--run", run, "--summary", "s", "--fixed", a,
                       "--not-fixed", "%s:r1" % b, "--not-fixed-file", nf_file)
        self.assertEqual(twice.returncode, 2)
        self.assertEqual(helpers.git(self.work, "rev-parse", "HEAD").strip(), head)
        nf_file = helpers.write_output(self.work, "not-fixed.json", json.dumps({b: helpers.TRAP}))
        summary = "initial value for `date` and $(whoami)"
        summary_file = helpers.write_output(self.work, "summary.txt", summary + "\n")
        trailer = "Co-Authored-By: Claude <noreply@anthropic.com>"
        commit = check_ok(self, self.h("ship-pr", "commit", "--run", run, "--summary-file", summary_file,
                                       "--fixed", a, "--not-fixed-file", nf_file, "--trailer", trailer))
        self.assertEqual(commit["not_fixed"], [b])
        self.assertEqual(helpers.git(self.work, "log", "-1", "--format=%B").strip(),
                         "fix(review): round 1 fixes — %s\n\n%s" % (summary, trailer))
        end = check_ok(self, self.h("ship-pr", "end-round", "--run", run))
        self.assertEqual(end["counts"]["duplicates"], 1)
        reg_path = (self.work / run).with_name(Path(run).name[: -len(".jsonl")] + ".registry.jsonl")
        reg = [json.loads(x) for x in reg_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["reason"] for x in reg if x["verdict"] == "NOT_FIXED"], [helpers.TRAP.strip()])
        self.assertEqual([x["of"] for x in reg if x["verdict"] == "DUPLICATE"], [a])


    def test_md07_x6_ship_pr_models_and_target_gate_run(self):
        """X6, MD-07: --models for this cycle; reviewers and validators logged with
        the model of their role. GT-07: the ship-pr.target gate opens the run,
        start continues it (one journal, gate events first)."""
        bad = self.h("ship-pr", "start", "--target", "main", "--models", "shippr.validator=gpt")
        self.assertEqual(bad.returncode, 2)
        self.assertFalse((self.work / ".harness" / "runs").exists())
        gate = check_ok(self, self.h("gate", "decide", "--id", "ship-pr.target"))
        self.assertEqual(gate["decision"], "ask")
        answer = helpers.write_output(self.work, "gate-answer.txt", "main\n")
        check_ok(self, self.h("gate", "answer", "--run", gate["run"], "--id", "ship-pr.target",
                              "--answer-file", answer))
        start = check_ok(self, self.h("ship-pr", "start", "--run", gate["run"], "--target", "main",
                                      "--themes", "logic", "--models", "shippr.validator=haiku"))
        self.assertEqual(start["run"], gate["run"])
        self.assertFalse(start["dirty"])
        self.assertEqual(start["models"], {"shippr.reviewer": {"model": "inherit", "pass_param": False},
                                           "shippr.validator": {"model": "haiku", "pass_param": True}})
        again = self.h("ship-pr", "start", "--run", gate["run"], "--target", "main")
        self.assertEqual(again.returncode, 2)
        run = start["run"]
        check_ok(self, self.h("ship-pr", "sync", "--run", run))
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title", "Sum", "--body-file", body))
        out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(
            [helpers.finding(file="src/sum.js", line=2, title="Empty crash")]))
        fid = check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", "logic",
                                    "--duration-ms", "9", "--output", out))["to_validate"][0]["id"]
        out = helpers.write_output(self.work, "validator.md", helpers.validator_block("REJECTED"))
        check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", fid, "--duration-ms", "3",
                              "--output", out))
        lines = journal.read(self.work / run)
        lines = [x for x in lines if x.get("event") != "autonomy"]    # AP-03: one autonomy event per run
        self.assertEqual([x.get("event", x.get("agent")) for x in lines], ["gate", "gate", "reviewer", "validator"])
        self.assertEqual([x["model"] for x in lines[2:]], ["inherit", "haiku"])
        self.assertEqual(lines[1]["answer"], "main")
        check_ok(self, self.h("journal", "validate", run))

    def test_consumed_transit_file_is_an_explicit_usage_error(self):
        """L1 remainder: a transit file under .harness/runs/ already consumed (or never
        written) is exit 2 with an explicit message, not [Errno 2]."""
        run = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--themes", "logic"))["run"]
        check_ok(self, self.h("ship-pr", "sync", "--run", run))
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title", "Sum", "--body-file", body))
        expected = ("transit file %s was already consumed or never written: rewrite it with Write "
                    "before this call" % body)
        cases = [("ship-pr", "pr", "--run", run, "--title", "Sum", "--body-file", body),
                 ("ship-pr", "commit", "--run", run, "--summary-file", ".harness/runs/summary.txt"),
                 ("ship-pr", "reviewer", "--run", run, "--theme", "logic", "--output", ".harness/runs/r.md")]
        for case in cases:
            res = self.h(*case)
            self.assertEqual(res.returncode, 2, case)
            self.assertIn("was already consumed or never written: rewrite it with Write before this call",
                          res.stderr, case)
            self.assertNotIn("Errno", res.stderr, case)
        self.assertIn(expected, self.h(*cases[0]).stderr)
        outside = self.h("ship-pr", "reviewer", "--run", run, "--theme", "logic", "--output", "missing.md")
        self.assertEqual(outside.returncode, 2)
        self.assertNotIn("transit file", outside.stderr)

    def _one_confirmed(self):
        """Run with an open PR and one CONFIRMED finding, fix applied, not committed."""
        run = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--themes", "logic"))["run"]
        check_ok(self, self.h("ship-pr", "sync", "--run", run))
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title", "Sum", "--body-file", body))
        out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(
            [helpers.finding(file="src/sum.js", line=2, title="Empty crash")]))
        fid = check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", "logic", "--model", "haiku",
                                    "--duration-ms", "9", "--output", out))["to_validate"][0]["id"]
        out = helpers.write_output(self.work, "validator.md", helpers.validator_block("CONFIRMED"))
        check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", fid, "--model", "haiku",
                              "--duration-ms", "3", "--output", out))
        (self.work / "src" / "sum.js").write_text(
            "function sum(v) {\n    return v.reduce((a, b) => a + b, 0);\n}\n", encoding="utf-8")
        return run, fid

    def test_not_fixed_file_duplicate_key_refused(self):
        """BACKLOG section 7, iteration 2: a key given twice in --not-fixed-file
        (json.loads would keep the last one silently) is refused, exit 2."""
        run, fid = self._one_confirmed()
        nf_file = helpers.write_output(self.work, "not-fixed.json",
                                       '{"%s": "first reason", "%s": "second reason"}' % (fid, fid))
        res = self.h("ship-pr", "commit", "--run", run, "--not-fixed-file", nf_file)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("given twice", res.stderr)
        self.assertIsNone(json.loads(Path(self.work / run).with_name(
            Path(run).name[: -len(".jsonl")] + ".state.json").read_text(encoding="utf-8"))["candidates"][fid]["outcome"])

    def test_multiline_summary_refused_before_any_commit(self):
        """BACKLOG section 7, iteration 2: a multi-line summary would glue a body to
        the subject without a blank line, on a commit that cannot be amended."""
        run, fid = self._one_confirmed()
        head = helpers.git(self.work, "rev-parse", "HEAD").strip()
        summary_file = helpers.write_output(self.work, "summary.txt", "first line\nsecond line\n")
        res = self.h("ship-pr", "commit", "--run", run, "--summary-file", summary_file, "--fixed", fid)
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn("single", res.stderr)
        self.assertEqual(helpers.git(self.work, "rev-parse", "HEAD").strip(), head)
        fake = helpers.FakeRunner()
        with self.assertRaises(ghflow.Stop):
            ghflow.commit_round(fake, {"candidates": {}, "round": 1}, registry.Registry(self.tmp / "x.jsonl"),
                                "a\nb", [], {})
        self.assertEqual(fake.calls, [])

    def test_transit_files_removed_once_consumed(self):
        """BACKLOG section 7, iteration 2: every transit file read by ship-pr under
        .harness/runs/ (title, body, drop reason, summary, not fixed) is removed
        once consumed, the call succeeded or not; a file elsewhere is kept."""
        runs_dir = self.work / ".harness" / "runs"
        run = check_ok(self, self.h("ship-pr", "start", "--target", "main", "--themes", "logic;b"))["run"]
        check_ok(self, self.h("ship-pr", "sync", "--run", run))
        transit = ("pr-title.txt", "pr-body.md", "drop.txt", "summary.txt", "not-fixed.json")
        names = lambda: sorted(p.name for p in runs_dir.iterdir() if p.name in transit)  # noqa: E731
        title = helpers.write_output(self.work, "pr-title.txt", "Sum\n")
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        self.assertEqual(self.h("ship-pr", "pr", "--run", run, "--title", "x", "--title-file", title,
                                "--body-file", body).returncode, 2)
        self.assertEqual(names(), [])
        title = helpers.write_output(self.work, "pr-title.txt", "Sum\n")
        body = helpers.write_output(self.work, "pr-body.md", "Body\n")
        check_ok(self, self.h("ship-pr", "pr", "--run", run, "--title-file", title, "--body-file", body))
        self.assertEqual(names(), [])
        found = [helpers.finding(file="src/sum.js", line=2, title="Empty crash"),
                 helpers.finding(file="src/sum.js", line=1, title="Naming")]
        out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(found[:1]))
        a = check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", "logic", "--model", "haiku",
                                  "--duration-ms", "9", "--output", out))["to_validate"][0]["id"]
        out = helpers.write_output(self.work, "reviewer.md", helpers.reviewer_block(found[1:]))
        b = check_ok(self, self.h("ship-pr", "reviewer", "--run", run, "--theme", "b", "--model", "haiku",
                                  "--duration-ms", "9", "--output", out))["to_validate"][0]["id"]
        reason = helpers.write_output(self.work, "drop.txt", "same subject\n")
        check_ok(self, self.h("ship-pr", "drop", "--run", run, "--finding", b, "--of", a, "--reason-file", reason))
        self.assertFalse((self.work / reason).exists())
        out = helpers.write_output(self.work, "validator.md", helpers.validator_block("CONFIRMED"))
        check_ok(self, self.h("ship-pr", "validator", "--run", run, "--finding", a, "--model", "haiku",
                              "--duration-ms", "3", "--output", out))
        (self.work / "src" / "sum.js").write_text(
            "function sum(v) {\n    return v.reduce((a, b) => a + b, 0);\n}\n", encoding="utf-8")
        summary = helpers.write_output(self.work, "summary.txt", "initial value\n")
        nf_file = helpers.write_output(self.work, "not-fixed.json", "{}")
        check_ok(self, self.h("ship-pr", "commit", "--run", run, "--summary-file", summary, "--fixed", a,
                              "--not-fixed-file", nf_file))
        self.assertEqual(names(), [])
        outside = self.work / "summary-outside.txt"
        outside.write_text("kept\n", encoding="utf-8")
        self.assertEqual(self.h("ship-pr", "drop", "--run", run, "--finding", a, "--of", b,
                                "--reason-file", "summary-outside.txt").returncode, 2)
        self.assertTrue(outside.exists())
        outside.unlink()


SKILLS_DIR = helpers.CORE.parent
SKILL_FILES = [SKILLS_DIR / "software-factory" / "SKILL.md", SKILLS_DIR / "ship-pr" / "SKILL.md"]
STDIN_OPTION = re.compile(r"--(?:output|source|body-file|content)\s+-(?![\w-])")


class UntrustedContentTest(helpers.TempDirCase):
    @unittest.skipUnless(all(p.is_file() for p in SKILL_FILES), "SKILL.md files absent (core copied into a project)")
    def test_skills_never_pipe_untrusted_content_through_stdin(self):
        """SF-04 FR-01 SP-13: no untrusted content (agent, skill, CI script, agent
        output, PR body) is passed to the core through stdin from a shell command."""
        for path in SKILL_FILES:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(STDIN_OPTION.findall(text), [], path)
            self.assertIn("Write", text, path)
            self.assertIn(".harness/runs/", text, path)

    @unittest.skipUnless(all(p.is_file() for p in SKILL_FILES), "SKILL.md files absent (core copied into a project)")
    def test_skills_prescribe_files_for_untrusted_text_and_new_commands(self):
        """BACKLOG section 7: ship-pr prescribes --summary-file, --not-fixed-file,
        --title-file (never the text on the command line), K drop, --trailer,
        quoted slots and the deferred journal completion; software-factory stops
        on a blocked copy-core and documents the deferred completion."""
        ship = (SKILLS_DIR / "ship-pr" / "SKILL.md").read_text(encoding="utf-8")
        for forbidden in ('--summary "', '--not-fixed "', '--title "', "--slot reviewer:", "`validator:<id>`"):
            self.assertFalse(forbidden in ship, forbidden)
        for required in ("--summary-file .harness/runs/", "--not-fixed-file .harness/runs/",
                         "--title-file .harness/runs/", "K drop --run <run> --finding <id> --of <id>",
                         '--trailer "', '--slot "reviewer:<theme>"', '--slot "validator:<id>"', "journal amend"):
            self.assertTrue(required in ship, required)
        sf = (SKILLS_DIR / "software-factory" / "SKILL.md").read_text(encoding="utf-8")
        for required in ("journal amend", "exit 1", "transit file"):
            self.assertTrue(required in sf, required)
        self.assertLessEqual(len(ship.splitlines()), 118)
        self.assertLessEqual(len(sf.splitlines()), 96)

    @unittest.skipUnless(all(p.is_file() for p in SKILL_FILES), "SKILL.md files absent (core copied into a project)")
    def test_skill_sizes_transit_rule_once_and_model_wiring(self):
        """Q-G: line counts and maximum line length never above HEAD 31579e0 (no
        joined lines); ship-pr states 'rewrite before each call' once, in its core
        section, for every phase; X1, X6 and MD-07 wiring prescribed."""
        limits = {"software-factory": (96, 1060), "ship-pr": (118, 1273)}
        texts = {}
        for name, (max_lines, max_width) in limits.items():
            text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            texts[name] = text
            lines = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")
        ship = texts["ship-pr"]
        self.assertEqual(ship.count("rewrite it before each call"), 1)
        core = ship[ship.index("## Deterministic core"):ship.index("## Mandatory rules")]
        self.assertIn("rewrite it before each call, in all phases", core)
        for required in ("--models", "pass_param", "shippr.reviewer", "shippr.validator", "never for `inherit`"):
            self.assertIn(required, ship, required)
        sf = texts["software-factory"]
        for required in ("--model-role factory.planner", "harness.model", "pass_param", "--models",
                         "never for `inherit`", "factory.verifier"):
            self.assertIn(required, sf, required)
        for text in texts.values():
            self.assertNotIn("--model <model or inherit>", text)

    def test_sf04_generated_file_transits_by_file_unchanged(self):
        """SF-04: an agent or script containing $ARGUMENTS, $1, $(date), backticks and
        apostrophes is installed byte for byte from the file written by Write."""
        src = helpers.write_output(self.tmp, "factory-builder.md", helpers.TRAP)
        res = helpers.harness(["sf", "install", "--dest", ".claude/agents/factory-builder.md", "--source", src],
                              self.tmp)
        self.assertEqual(json.loads(res.stdout)[".claude/agents/factory-builder.md"], fusion.CREATED, res.stderr)
        self.assertEqual((self.tmp / ".claude" / "agents" / "factory-builder.md").read_bytes(),
                         helpers.TRAP.encode("utf-8"))


class TempCleanupTest(unittest.TestCase):
    def test_read_only_git_objects_are_removed(self):
        """Throwaway repositories (read-only git objects) do not survive the test."""
        tmp = Path(tempfile.mkdtemp(prefix="hcore-"))
        try:
            helpers.init_repo(tmp / "repo")
            (tmp / "repo" / "f.txt").write_text("x\n", encoding="utf-8")
            helpers.git(tmp / "repo", "add", "-A")
            helpers.git(tmp / "repo", "commit", "-q", "-m", "x")
            locked = tmp / "locked.txt"
            locked.write_text("ro", encoding="utf-8")
            os.chmod(str(locked), stat.S_IREAD)
            helpers.remove_tree(tmp)
            self.assertFalse(tmp.exists())
        finally:
            if tmp.exists():
                shutil.rmtree(str(tmp), ignore_errors=True)
