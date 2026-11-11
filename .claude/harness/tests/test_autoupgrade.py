# Automatic upgrade proposed by the doctor: UA-01 to UA-04.

import json
import unittest

import helpers
from hcore import autoupgrade

PLANS = helpers.FIXTURES / "upgrade-plans"


def load(name):
    return json.loads((PLANS / ("%s.json" % name)).read_text(encoding="utf-8"))


@unittest.skipUnless(PLANS.is_dir(), helpers.SKIP_FIXTURES)
class DecisionTest(unittest.TestCase):
    def test_ua01_blocks_stamp_and_untouched_core_are_applied(self):
        """UA-01, CA-20: only blocks, or stamp and an unmodified core file -> apply (policy auto)."""
        for name in ("blocks-only", "stamp-and-core"):
            self.assertEqual(autoupgrade.decide(load(name), "auto")["action"], "apply", name)

    def test_ua02_anything_else_is_proposed_with_the_changes_in_cause(self):
        """UA-02: outside, divergent block, migration or modified core -> propose; the causes are listed."""
        outside = autoupgrade.decide(load("outside"), "auto")
        self.assertEqual((outside["action"], [c["kind"] for c in outside["causes"]]), ("propose", ["outside"]))
        divergent = autoupgrade.decide(load("divergent"), "auto")
        self.assertEqual((divergent["action"], divergent["causes"][0]["kind"]), ("propose", "divergent-block"))
        migration = autoupgrade.decide(load("migration"), "auto")
        self.assertEqual(sorted(c["kind"] for c in migration["causes"]), ["core", "migration"])

    def test_ua02_under_gate_the_gate_is_asked(self):
        """UA-02, CA-20: policy gate -> ask, for any non-empty plan."""
        self.assertEqual(autoupgrade.decide(load("blocks-only"), "gate")["action"], "ask")

    def test_ua04_an_empty_plan_means_nothing_to_do(self):
        """UA-04, CA-20: an empty plan (the second pass after an upgrade) -> none, under both policies."""
        for policy in ("auto", "gate"):
            self.assertEqual(autoupgrade.decide(load("empty"), policy)["action"], "none")

    def test_ua01_the_owner_skill_gives_the_command(self):
        """UA-01: the command is the --upgrade of the owner skill."""
        d = autoupgrade.decide({"changes": [{"path": ".claude/rgkb/rgkb.py", "kind": "block"},
                                            {"path": ".claude/agents/x.md", "kind": "block"}]}, "auto")
        self.assertEqual(d["commands"], ["/code-index --upgrade", "/software-factory --upgrade"])

    def test_ua01_the_cli_reads_a_plan_file(self):
        """UA-01: `doctor auto-upgrade --plan` with the flag --autonomy."""
        res = helpers.harness(["doctor", "auto-upgrade", "--plan", str(PLANS / "blocks-only.json"), "--autonomy", "auto"],
                              helpers.CORE)
        self.assertEqual(json.loads(res.stdout)["action"], "apply", res.stderr)
        res = helpers.harness(["doctor", "auto-upgrade", "--plan", str(PLANS / "blocks-only.json")], helpers.CORE)
        self.assertEqual(res.returncode, 0)


class ScanTest(unittest.TestCase):
    def test_ua03_a_scan_only_proposes(self):
        """UA-03: `doctor scan` has no way to launch an upgrade (no auto-upgrade option takes a root list)."""
        import harness
        args = harness.build_parser().parse_args(["doctor", "scan", "--depth", "1"])
        self.assertEqual(args.action, "scan")
        self.assertIsNone(args.plan)


if __name__ == "__main__":
    unittest.main()
