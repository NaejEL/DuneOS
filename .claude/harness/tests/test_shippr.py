# Module hcore.shippr: ship-pr review loop (SP-05 to SP-10).

import json
import unittest

import helpers
from hcore import registry, shippr

THEMES = ["logic", "regressions", "consistency"]


class LoopCase(helpers.TempDirCase):
    def setUp(self):
        super().setUp()
        self.reg = registry.Registry(self.tmp / "ship-pr-20261016-120000.registry.jsonl")
        self.state = shippr.new_state("main", 3, THEMES)

    def review(self, theme, findings=None, dead=False, raw=None):
        out = raw if raw is not None else helpers.reviewer_block(findings or [])
        return shippr.record_reviewer(self.state, self.reg, theme, None if dead else out, dead)

    def validate(self, fid, verdict=None, dead=False):
        out = None if dead else helpers.validator_block(verdict)
        return shippr.record_validator(self.state, self.reg, fid, out, dead)

    def registry_lines(self):
        return [json.loads(x) for x in self.reg.path.read_text(encoding="utf-8").splitlines()]


class ThemeDeathTest(LoopCase):
    def test_sp05_dead_theme_relaunched_once_then_uncovered(self):
        """SP-05."""
        self.assertEqual(self.review("logic", dead=True)[0]["action"], "relaunch")
        decision, log = self.review("logic", raw="truncated output")
        self.assertEqual(decision["action"], "uncovered")
        self.assertEqual(log["verdict"], "DEAD")
        self.review("regressions")
        self.review("consistency")
        result = shippr.end_round(self.state, "sha1")
        self.assertNotEqual(result["exit"], "exhaustion")
        # No early exit on the silence of a dead theme: next round.
        self.assertIsNone(result["exit"])
        self.assertEqual(result["next_round"], 2)
        self.assertEqual(result["uncovered"], [{"round": 1, "theme": "logic"}])
        self.assertIsNotNone(shippr.merge_refusal(self.state))
        for theme in THEMES:
            self.review(theme)
        self.assertEqual(shippr.end_round(self.state, "sha2")["exit"], "exhaustion")
        self.assertIn("logic (round 1)", shippr.merge_refusal(self.state))

    def test_sp05_uncovered_silence_never_exits_until_cap(self):
        """SP-05: rounds without CONFIRMED but with an uncovered theme -> the loop
        goes on until the cap; the merge stays refused."""
        for number in (1, 2, 3):
            self.review("logic", dead=True)
            self.review("logic", dead=True)
            self.review("regressions")
            self.review("consistency")
            result = shippr.end_round(self.state, "sha%d" % number)
            self.assertEqual(result["exit"], None if number < 3 else "cap")
        self.assertEqual(len(self.state["uncovered"]), 3)
        self.assertIn("not covered", shippr.merge_refusal(self.state))

    def test_sp05_silent_theme_blocks_early_exit(self):
        """SP-05."""
        self.review("logic")
        self.review("regressions")
        with self.assertRaises(shippr.LoopError):
            shippr.end_round(self.state, "sha1")

    def test_sp05_dead_theme_with_confirmed_continues_but_merge_refused(self):
        """SP-05."""
        self.review("logic", dead=True)
        self.review("logic", dead=True)
        fid = self.review("regressions", [helpers.finding()])[0]["to_validate"][0]["id"]
        self.review("consistency")
        self.validate(fid, "CONFIRMED")
        shippr.set_outcome(self.state, self.reg, fid, True, commit="abc")
        self.assertEqual(shippr.end_round(self.state, "sha1")["next_round"], 2)
        for theme in THEMES:
            self.review(theme)
        self.assertEqual(shippr.end_round(self.state, "sha2")["exit"], "exhaustion")
        self.assertIsNotNone(shippr.merge_refusal(self.state))


class ValidatorDeathTest(LoopCase):
    def test_sp06_dead_validator_leaves_finding_unvalidated(self):
        """SP-06."""
        fid = self.review("logic", [helpers.finding()])[0]["to_validate"][0]["id"]
        self.assertEqual(self.validate(fid, dead=True)[0]["action"], "relaunch")
        decision, log = self.validate(fid, dead=True)
        self.assertEqual(decision["action"], "unvalidated")
        self.assertEqual(self.registry_lines()[-1]["verdict"], "UNVALIDATED")
        self.assertEqual(shippr.confirmed_of_round(self.state), [])
        with self.assertRaises(shippr.LoopError):
            shippr.set_outcome(self.state, self.reg, fid, True, commit="abc")


class InterruptedRelaunchTest(LoopCase):
    def test_sp07_validator_replayed_after_lost_state_single_registry_line(self):
        """SP-07: verdict written to the registry, state not saved (interruption),
        then the same call replayed on the previous state: a single registry line."""
        fid = self.review("logic", [helpers.finding()])[0]["to_validate"][0]["id"]
        saved = json.loads(json.dumps(self.state))
        self.validate(fid, "REJECTED")
        self.state = saved
        self.reg = registry.Registry(self.reg.path)
        self.assertEqual(self.validate(fid, "REJECTED")[0]["action"], "drop")
        self.assertEqual(len(self.registry_lines()), 1)


class DedupTest(LoopCase):
    def test_sp07_rejected_finding_does_not_reach_validator_again(self):
        """SP-07."""
        rejected = self.review("logic", [helpers.finding(title="False bug")])[0]["to_validate"][0]["id"]
        self.validate(rejected, "REJECTED")
        real = self.review("regressions", [helpers.finding(file="b.py", title="Real bug")])[0]["to_validate"][0]["id"]
        self.validate(real, "CONFIRMED")
        self.review("consistency")
        shippr.set_outcome(self.state, self.reg, real, True, commit="c")
        self.assertEqual(shippr.end_round(self.state, "sha1")["next_round"], 2)
        decision, log = self.review("logic", [helpers.finding(title="false  BUG !")])
        self.assertEqual(decision["action"], "none")
        self.assertEqual(decision["to_validate"], [])
        self.assertEqual(decision["duplicates"][0]["previous"], "REJECTED")
        self.assertEqual(log["rejected"], 1)

    def test_sp07_same_finding_from_two_themes_validated_once(self):
        """SP-07."""
        first = self.review("logic", [helpers.finding()])[0]
        second = self.review("regressions", [helpers.finding()])[0]
        self.assertEqual(len(first["to_validate"]), 1)
        self.assertEqual(second["to_validate"], [])
        self.assertEqual(second["duplicates"][0]["previous"], "PENDING")


class SemanticDuplicateTest(LoopCase):
    def test_sp07_drop_semantic_duplicate_counted_and_not_awaited(self):
        """SP-07 (BACKLOG section 7, review friction): a PENDING candidate, same
        subject as another under another title, is dropped as a duplicate: state
        and registry, counted in duplicates, end_round no longer waits for it."""
        a = self.review("logic", [helpers.finding(title="Sum of empty list crashes")])[0]["to_validate"][0]["id"]
        b = self.review("regressions", [helpers.finding(title="reduce() without initial value")])[0]["to_validate"][0]["id"]
        self.review("consistency")
        with self.assertRaises(shippr.LoopError):
            shippr.drop_duplicate(self.state, self.reg, b, "R9-F9")
        with self.assertRaises(shippr.LoopError):
            shippr.drop_duplicate(self.state, self.reg, b, b)
        result = shippr.drop_duplicate(self.state, self.reg, b, a, "same crash as %s" % a)
        self.assertEqual((result["action"], result["of"]), ("dropped", a))
        self.assertEqual(self.state["candidates"][b]["status"], "DUPLICATE")
        last = self.registry_lines()[-1]
        self.assertEqual((last["verdict"], last["id"], last["of"], last["reason"]),
                         ("DUPLICATE", b, a, "same crash as %s" % a))
        with self.assertRaises(shippr.LoopError):
            shippr.drop_duplicate(self.state, self.reg, b, a)
        with self.assertRaises(shippr.LoopError):
            self.validate(b, "CONFIRMED")
        self.validate(a, "REJECTED")
        result = shippr.end_round(self.state, "s")
        self.assertEqual(result["exit"], "exhaustion")
        self.assertEqual((result["counts"]["duplicates"], result["counts"]["raw"]), (1, 2))
        # The dropped key never comes back to life in a later review.
        self.assertTrue(self.reg.known(self.state["candidates"][b]["key"]))

    def test_sp07_drop_refuses_a_cycle_or_a_chain_of_duplicates(self):
        """SP-07 (iteration 2): drop A --of B then drop B --of A would make both
        DUPLICATE and the real finding vanish: the target must not be a duplicate."""
        a = self.review("logic", [helpers.finding(title="Crash on empty")])[0]["to_validate"][0]["id"]
        b = self.review("regressions", [helpers.finding(title="reduce on []")])[0]["to_validate"][0]["id"]
        c = self.review("consistency", [helpers.finding(title="no initial value")])[0]["to_validate"][0]["id"]
        shippr.drop_duplicate(self.state, self.reg, a, b)
        with self.assertRaises(shippr.LoopError) as ctx:
            shippr.drop_duplicate(self.state, self.reg, b, a)
        self.assertIn("itself a duplicate", str(ctx.exception))
        with self.assertRaises(shippr.LoopError):
            shippr.drop_duplicate(self.state, self.reg, c, a)
        self.assertEqual(self.state["candidates"][b]["status"], "PENDING")
        self.assertEqual(self.state["candidates"][c]["status"], "PENDING")
        shippr.drop_duplicate(self.state, self.reg, c, b)
        with self.assertRaises(shippr.LoopError):
            shippr.end_round(self.state, "s")   # b, the real finding, still awaits its validator
        self.validate(b, "REJECTED")
        self.assertEqual(shippr.end_round(self.state, "s")["counts"]["duplicates"], 2)

    def test_sp07_drop_refuses_a_decided_finding(self):
        a = self.review("logic", [helpers.finding(title="x")])[0]["to_validate"][0]["id"]
        b = self.review("regressions", [helpers.finding(title="y")])[0]["to_validate"][0]["id"]
        self.validate(b, "REJECTED")
        with self.assertRaises(shippr.LoopError):
            shippr.drop_duplicate(self.state, self.reg, b, a)


class ExitTest(LoopCase):
    def full_round(self, confirmed):
        fids = []
        for i, theme in enumerate(THEMES):
            findings = [helpers.finding(title="bug %d-%d" % (self.state["round"], i))] if confirmed else []
            fids += [f["id"] for f in self.review(theme, findings)[0]["to_validate"]]
        for fid in fids:
            self.validate(fid, "CONFIRMED")
            shippr.set_outcome(self.state, self.reg, fid, True, commit="c")
        return shippr.end_round(self.state, "sha%d" % self.state["round"])

    def test_sp08_exhaustion(self):
        """SP-08."""
        self.assertEqual(self.full_round(True)["next_round"], 2)
        self.assertEqual(self.full_round(False)["exit"], "exhaustion")

    def test_sp08_cap(self):
        """SP-08."""
        self.state["rounds"] = 2
        self.full_round(True)
        result = self.full_round(True)
        self.assertEqual(result["exit"], "cap")
        with self.assertRaises(shippr.LoopError):
            self.review("logic")

    def test_sp08_prev_round_sha_recorded(self):
        """SP-08."""
        self.full_round(True)
        self.assertEqual(self.state["prev_round_sha"], "sha1")

    def test_sp15_reference_is_head_before_fix_commit(self):
        """SP-15: the reference of the next round is HEAD before the fix."""
        fid = self.review("logic", [helpers.finding()])[0]["to_validate"][0]["id"]
        self.review("regressions")
        self.review("consistency")
        self.validate(fid, "CONFIRMED")
        shippr.mark_pre_fix(self.state, "before-fix")
        shippr.mark_pre_fix(self.state, "ignore")
        shippr.set_outcome(self.state, self.reg, fid, True, commit="fix1")
        shippr.end_round(self.state, "fix1")
        self.assertEqual(self.state["prev_round_sha"], "before-fix")

    def test_sp17_round_does_not_end_on_an_unpushed_fix_commit(self):
        """SP-17 (BACKLOG section 7): a fix commit whose push failed blocks end-round."""
        fid = self.review("logic", [helpers.finding()])[0]["to_validate"][0]["id"]
        self.review("regressions")
        self.review("consistency")
        self.validate(fid, "CONFIRMED")
        shippr.set_outcome(self.state, self.reg, fid, True, commit="fix1")
        self.state["unpushed"] = {"round": 1, "hash": "fix1", "summary": "s", "fixed": [fid], "not_fixed": []}
        with self.assertRaises(shippr.LoopError) as ctx:
            shippr.end_round(self.state, "fix1")
        self.assertIn("not pushed", str(ctx.exception))
        self.state["unpushed"] = None
        self.assertEqual(shippr.end_round(self.state, "fix1")["next_round"], 2)


class OutcomeTest(LoopCase):
    def test_sp09_confirmed_not_fixed_is_recorded(self):
        """SP-09."""
        fid = self.review("logic", [helpers.finding()])[0]["to_validate"][0]["id"]
        self.validate(fid, "CONFIRMED")
        self.review("regressions")
        self.review("consistency")
        with self.assertRaises(shippr.LoopError):
            shippr.end_round(self.state, "s")
        with self.assertRaises(shippr.LoopError):
            shippr.set_outcome(self.state, self.reg, fid, False, reason="")
        shippr.set_outcome(self.state, self.reg, fid, False, reason="false positive found while fixing")
        last = self.registry_lines()[-1]
        self.assertEqual(last["verdict"], "NOT_FIXED")
        self.assertEqual(last["reason"], "false positive found while fixing")
        self.assertEqual(shippr.summary(self.state)["left_open"][0]["id"], fid)


class CountersTest(LoopCase):
    def test_sp10_raw_confirmed_rejected_per_round(self):
        """SP-10."""
        out = self.review("logic", [helpers.finding(title="a"), helpers.finding(title="b"),
                                      helpers.finding(title="a")])[0]
        a, b = [f["id"] for f in out["to_validate"]]
        self.validate(a, "CONFIRMED")
        self.validate(b, "REJECTED")
        self.review("regressions")
        self.review("consistency")
        shippr.set_outcome(self.state, self.reg, a, True, commit="c")
        result = shippr.end_round(self.state, "s")
        self.assertEqual(result["counts"], {"round": 1, "raw": 3, "confirmed": 1, "rejected": 1,
                                            "duplicates": 1, "unvalidated": 0})
        self.assertEqual(shippr.summary(self.state)["rounds"][0]["raw"], 3)


if __name__ == "__main__":
    unittest.main()
