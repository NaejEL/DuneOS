# Chaining: headless children and delivery commits (CH-01 to CH-06, AP-04).
#
# CH-05 (brainstorm launches nothing, its sentence is kept) is checked by `git diff` of skills/brainstorm
# at verification time (CA-17, CA-40): nothing in this test reads a SKILL.md.

import json
import os
import re
import subprocess
import time
import unittest

import helpers
import phase4
from hcore import chain, journal, runs
from hcore.proc import Runner


def out(test, res, code=0):
    test.assertEqual(res.returncode, code, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout) if res.stdout.strip() else None


class ArgumentsTest(unittest.TestCase):
    def test_ch03_options_are_taken_out_of_the_arguments(self):
        """CH-03, AP-03: --ship, --no-ship, --deliver-only, --worktree, --chained-from, --chain-id, --autonomy."""
        rest, o = chain.extract_options('specs/SPEC-a.md --ship main --deliver-only --autonomy auto --chain-id 7 '
                                        '--chained-from run1 --worktree')
        self.assertEqual(rest, "specs/SPEC-a.md")
        self.assertEqual((o["ship"], o["deliver_only"], o["autonomy"], o["chain_id"], o["chained_from"], o["worktree"]),
                         ("main", True, "auto", "7", "run1", True))
        self.assertTrue(chain.extract_options("a need --no-ship")[1]["no_ship"])
        with self.assertRaises(chain.ChainError):
            chain.extract_options("x --autonomy maybe")
        with self.assertRaises(chain.ChainError):
            chain.extract_options("x --ship main --no-ship")

    def test_ch01_the_argv_is_a_list_with_the_wanted_options(self):
        """CH-01: -p, the skill, --permission-prompts none, --max-turns, --permission-mode acceptEdits, --autonomy."""
        argv = chain.build_argv("factory-run", "specs/SPEC-a.md --ship main", ["Read", "Edit"], 100, "auto")
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertTrue(argv[2].startswith("/factory-run specs/SPEC-a.md") and argv[2].endswith("--autonomy auto"))
        for pair in (("--permission-prompts", "none"), ("--max-turns", "100"), ("--permission-mode", "acceptEdits"),
                     ("--allowedTools", "Read,Edit"), ("--output-format", "json")):
            self.assertEqual(argv[argv.index(pair[0]) + 1], pair[1])
        with self.assertRaises(chain.ChainError):
            chain.build_argv("brainstorm", "x", [], 1)

    def test_ch01_the_child_environment(self):
        """CH-01: the parent's variables plus HARNESS_CHAIN_ID and HARNESS_PARENT_RUN, minus CLAUDECODE."""
        env = chain.child_env({"CLAUDECODE": "1", "PATH": "p"}, "id1", "run1")
        self.assertEqual(env, {"PATH": "p", "HARNESS_CHAIN_ID": "id1", "HARNESS_PARENT_RUN": "run1"})

    def test_ch01_cost_guard(self):
        """CH-01: a billing variable stops unless chain.allow_api_billing is true."""
        self.assertEqual(chain.cost_guard({"ANTHROPIC_API_KEY": "x"}, {}), "ANTHROPIC_API_KEY")
        self.assertEqual(chain.cost_guard({"CLAUDE_CODE_USE_VERTEX": "1"}, {}), "CLAUDE_CODE_USE_VERTEX")
        self.assertIsNone(chain.cost_guard({"ANTHROPIC_API_KEY": "x"}, {"allow_api_billing": True}))
        self.assertIsNone(chain.cost_guard({"ANTHROPIC_API_KEY": ""}, {}))

    def test_ch01_turns_and_tools(self):
        """CH-01 (Q28): 100 turns for factory-run, 200 with ship-pr; gh and Skill are added when ship-pr is chained."""
        self.assertEqual((chain.MAX_TURNS["factory-run"], chain.MAX_TURNS["ship-pr"]), (100, 200))
        with helpers.tempfile.TemporaryDirectory() as tmp:
            tools = chain.allowed_tools(helpers.Path(tmp), "ship-pr", {"allowed_tools": ["Read"]})
        self.assertEqual(tools, ["Read", "Bash(gh *)", "Skill"])


class SpawnTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.work, self.remote = phase4.project(self.tmp, {"SPEC-a.md": phase4.spec_text("APPROUVEE", "Add the thing")})
        self.env = phase4.env_for(self.tmp)

    def spawn(self, parent=None, extra_env=None, args="specs/SPEC-a.md --ship main"):
        src = helpers.write_output(self.work, "chain-args.txt", args)
        cmd = ["chain", "spawn", "--root", str(self.work), "--skill", "factory-run", "--args-file", src,
               "--autonomy", "auto"] + (["--parent-run", parent] if parent else [])
        return helpers.harness(cmd, self.work, env=dict(self.env, **(extra_env or {})))

    def wait(self, chain_id, seconds=60):
        return helpers.harness(["chain", "wait", "--root", str(self.work), "--id", chain_id, "--max-seconds", str(seconds)],
                               self.work, env=self.env)

    def test_ch01_spawn_runs_the_child_without_a_shell_and_without_claudecode(self):
        """CH-01, CA-16: argv of the child, environment without CLAUDECODE with HARNESS_CHAIN_ID, outputs and card."""
        phase4.scenario(self.tmp, {"SPEC-a.md": {"action": "open"}})
        card = out(self, self.spawn(extra_env={"CLAUDECODE": "1"}))
        done = out(self, self.wait(card["id"]))
        self.assertTrue(done["done"])
        self.assertEqual((done["status"], done["exit_code"]), ("done", 0))
        call = phase4.claude_calls(self.tmp)[0]
        argv = call["argv"]
        self.assertIn("-p", argv)
        self.assertTrue(any(a.startswith("/factory-run") for a in argv))
        for pair in (("--permission-prompts", "none"), ("--max-turns", "200"), ("--permission-mode", "acceptEdits")):
            self.assertEqual(argv[argv.index(pair[0]) + 1], pair[1])
        self.assertIsNone(call["env"]["CLAUDECODE"])
        self.assertEqual(call["env"]["HARNESS_CHAIN_ID"], card["id"])
        card_file = self.work / ".harness" / "runs" / ("chain-%s.json" % card["id"])
        data = json.loads(card_file.read_text(encoding="utf-8"))
        self.assertEqual({"id", "skill", "args", "pid", "root", "parent_run", "started", "status"} - set(data), set())
        self.assertTrue((self.work / ".harness" / "runs" / ("chain-%s.out.json" % card["id"])).is_file())

    def test_ch01_max_turns_is_100_for_factory_run_alone(self):
        """CH-01, CA-16: --max-turns 100 when ship-pr is not chained."""
        card = out(self, self.spawn(args="specs/SPEC-a.md"))
        out(self, self.wait(card["id"]))
        argv = phase4.claude_calls(self.tmp)[0]["argv"]
        self.assertEqual(argv[argv.index("--max-turns") + 1], "100")

    def test_ch01_the_cost_guard_launches_nothing(self):
        """CH-01, CA-16: ANTHROPIC_API_KEY -> exit 1, STOP of kind cost, no process; allowed by the stamp -> launched."""
        res = self.spawn(extra_env={"ANTHROPIC_API_KEY": "x"})
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("STOP:", res.stderr)
        self.assertIn("— cost", sorted((self.work / ".harness" / "runs").glob("STOP-*.md"))[0].read_text(encoding="utf-8"))
        self.assertEqual(phase4.claude_calls(self.tmp), [])
        phase4.stamp(self.work, chain={"allow_api_billing": True})
        card = out(self, self.spawn(extra_env={"ANTHROPIC_API_KEY": "x"}))
        out(self, self.wait(card["id"]))
        self.assertEqual(len(phase4.claude_calls(self.tmp)), 1)

    def test_ch01_claude_absent_is_a_child_stop(self):
        """CH-01: no claude on the PATH -> stop of kind child, nothing launched."""
        env = dict(self.env, HCORE_CLAUDE="", PATH=os.path.dirname(os.sys.executable))
        res = helpers.harness(["chain", "spawn", "--root", str(self.work), "--skill", "factory-run", "--args-file",
                               helpers.write_output(self.work, "chain-args.txt", "x")], self.work, env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("claude is not on the PATH", res.stderr)

    def test_ch01_timeout_kills_the_child_and_stops(self):
        """CH-01, CA-16: past chain.timeout_min the tree is killed and the wait stops with kind child."""
        phase4.stamp(self.work, chain={"timeout_min": 0.03})
        phase4.scenario(self.tmp, {"SPEC-a.md": {"action": "sleep", "seconds": 60}})
        started = time.time()
        card = out(self, self.spawn())
        res = self.wait(card["id"], 30)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("exceeded chain.timeout_min", res.stderr)
        self.assertLess(time.time() - started, 40)
        self.assertTrue(any("— child" in p.read_text(encoding="utf-8")
                            for p in (self.work / ".harness" / "runs").glob("STOP-*.md")))

    def test_ch01_a_failing_child_is_a_child_stop_that_cites_its_own_stop(self):
        """CH-01, SX-01: exit code 1 and a STOP file left by the child -> the wait stops (kind child)."""
        phase4.scenario(self.tmp, {"SPEC-a.md": {"action": "stop"}})
        card = out(self, self.spawn())
        res = self.wait(card["id"])
        self.assertEqual(res.returncode, 1)
        self.assertRegex(res.stderr, r"STOP: the child exited with code 1")

    def test_ch01_a_child_that_exits_0_with_is_error_in_its_output_is_a_failure(self):
        """CH-01: exit 0 but `is_error` (max turns, auth error) in out.json -> the chain is a failure."""
        card = out(self, self.spawn())
        out(self, self.wait(card["id"]))
        folder = self.work / ".harness" / "runs"
        (folder / ("chain-%s.out.json" % card["id"])).write_text(
            json.dumps({"subtype": "error_max_turns", "is_error": True, "result": "max turns"}), encoding="utf-8")
        result = chain.status(self.work, card["id"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("max turns", chain.failure(result))

    def test_ch01_a_dead_wrapper_ends_the_wait_instead_of_looping_forever(self):
        """CH-01: the wrapper vanished before writing its record -> the chain is failed and the wait returns."""
        folder = self.work / ".harness" / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        dead = subprocess.Popen([os.sys.executable, "-c", "pass"])
        dead.wait()
        card = {"id": "dead", "skill": "factory-run", "args": "x", "pid": dead.pid, "root": str(self.work),
                "cwd": str(self.work), "parent_run": None, "started": time.time(), "status": "running",
                "argv": [], "timeout_s": 60, "exit_code": None, "stops_before": [],
                "out": str(folder / "chain-dead.out.json"), "err": str(folder / "chain-dead.err.txt")}
        chain.write_card(self.work, card)
        result = chain.wait(self.work, "dead", 10)
        self.assertTrue(result["done"])
        self.assertIn("wrapper died", chain.failure(result))

    def test_ch06_the_chain_event_is_journaled(self):
        """CH-06: a `chain` event with id, skill, args, parent_run and result in the parent journal."""
        parent = runs.create(self.work, "factory-run", {"status": "running"})
        card = out(self, self.spawn(parent=str(parent)))
        out(self, self.wait(card["id"]))
        event = [x for x in journal.read(parent) if x.get("event") == "chain"][0]
        self.assertEqual((event["id"], event["skill"], event["result"]), (card["id"], "factory-run", "started"))
        self.assertEqual(journal.validate_file(parent), [])

    def test_ap04_the_effective_policy_travels_to_the_child(self):
        """AP-04: the child receives --autonomy <effective>."""
        card = out(self, self.spawn())
        out(self, self.wait(card["id"]))
        self.assertTrue(phase4.claude_calls(self.tmp)[0]["argv"][1].endswith("--autonomy auto"))


class DeliverTest(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.work, _ = phase4.project(self.tmp, {"SPEC-feature-x.md": phase4.spec_text("LIVREE", "Feature X")})
        self.run = Runner(cwd=str(self.work))

    def state(self, **extra):
        return dict({"spec": "specs/SPEC-feature-x.md", "profile": "software", "dirty_at_start": False}, **extra)

    def test_ch02_clean_tree_on_the_target_gets_a_factory_branch_and_a_commit(self):
        """CH-02, CA-17: branch factory/<slug>, `feat(factory): <title>` plus trailers, no push."""
        (self.work / "new.txt").write_text("x", encoding="utf-8")
        result = chain.deliver(self.run, self.work, self.state(), "main", ["Co-Authored-By: Someone <a@b.c>"])
        self.assertEqual(result["branch"], "factory/feature-x")
        message = helpers.git(self.work, "log", "-1", "--format=%B").strip()
        self.assertRegex(message.splitlines()[0], r"^feat\(factory\): .+")
        self.assertIn("Co-Authored-By: Someone", message)
        self.assertEqual(helpers.git(self.work, "rev-parse", "--abbrev-ref", "HEAD").strip(), "factory/feature-x")
        self.assertEqual(helpers.git(self.work, "ls-remote", "--heads", "origin").count("factory/"), 0)
        self.assertEqual(helpers.git(self.work, "status", "--porcelain").strip(), "")

    def test_ch02_an_existing_branch_gets_a_timestamp_suffix(self):
        """CH-02 (Q8): the branch name is never reused."""
        helpers.git(self.work, "branch", "factory/feature-x")
        (self.work / "new.txt").write_text("x", encoding="utf-8")
        result = chain.deliver(self.run, self.work, self.state(), "main")
        self.assertRegex(result["branch"], r"^factory/feature-x-[0-9]{8}-[0-9]{6}$")

    def test_ch02_conditions_a_dirty_tree_at_start_means_no_commit(self):
        """CH-02, CA-17: dirty_at_start, no target, another profile or no spec -> reasons, nothing committed."""
        self.assertEqual(chain.deliver_conditions(self.state(), "main"), [])
        self.assertTrue(chain.deliver_conditions(self.state(dirty_at_start=True), "main"))
        self.assertTrue(chain.deliver_conditions(self.state(), None))
        self.assertTrue(chain.deliver_conditions(self.state(profile="research"), "main"))
        self.assertTrue(chain.deliver_conditions({"profile": "software"}, "main"))

    def test_ch02_the_target_is_resolved_from_the_flag_the_stamp_then_origin_head(self):
        """CH-02 (Q21)."""
        self.assertEqual(chain.resolve_target(self.run, self.work, "release"), "release")
        phase4.stamp(self.work, ship_pr={"target": "develop"})
        self.assertEqual(chain.resolve_target(self.run, self.work, None), "develop")
        (self.work / ".claude" / "harness.json").unlink()
        helpers.git(self.work, "remote", "set-head", "origin", "main")
        self.assertEqual(chain.resolve_target(self.run, self.work, None), "main")

    def test_ch03_factory_run_start_reads_the_chain_options_and_keeps_the_spec_route(self):
        """CH-03, AP-03: `specs/SPEC-x.md --ship main --autonomy auto` is still a build entry; the options are kept in the state."""
        phase4.write_spec(self.work, "SPEC-b.md", phase4.spec_text("APPROUVEE", "B"))
        helpers.git(self.work, "add", "-A")
        helpers.git(self.work, "commit", "-q", "-m", "spec b")
        src = helpers.write_output(self.work, "factory-run-arguments.txt",
                                   "specs/SPEC-b.md --ship main --deliver-only --autonomy auto --chain-id c1")
        d = out(self, helpers.harness(["factory-run", "start", "--arguments-file", src], self.work))
        self.assertEqual((d["mode"], d["autonomy"], d["options"]["ship"], d["options"]["deliver_only"]),
                         ("build", "auto", "main", True))
        state = runs.load_state(self.work / d["run"])
        self.assertEqual((state["spec"], state["chain_id"], state["dirty_at_start"]), ("specs/SPEC-b.md", "c1", False))
        self.assertIn("autonomy", [x.get("event") for x in journal.read(self.work / d["run"])])

    def test_ch02_deliver_through_the_cli_skips_a_dirty_start(self):
        """CH-02, CA-17: the command journals the reason and commits nothing when the tree was dirty at start."""
        (self.work / "wip.txt").write_text("user work", encoding="utf-8")
        run = runs.create(self.work, "factory-run", {"status": "success", "spec": "specs/SPEC-feature-x.md",
                                                     "profile": "software", "dirty_at_start": True, "autonomy": "auto"})
        head = helpers.git(self.work, "rev-parse", "HEAD")
        d = out(self, helpers.harness(["factory-run", "deliver", "--run", str(run)], self.work))
        self.assertFalse(d["delivered"])
        self.assertEqual(helpers.git(self.work, "rev-parse", "HEAD"), head)
        result = [x for x in journal.read(run) if x.get("event") == "chain"][0]["result"]
        self.assertTrue(result.startswith("skipped"))


if __name__ == "__main__":
    unittest.main()
