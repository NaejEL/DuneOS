# brainstorm rules (BS-01 to BS-12) and the "nothing written before the final gate" rule (X5).

import json
import unittest
from pathlib import Path

import helpers
from hcore import bs, gates, journal, verdicts


def piste(pid, title, origine="hors-enonce", lens="hypotheses"):
    return {"id": pid, "lens": lens, "origine": origine, "titre": title, "these": "t", "appui": "a",
            "consequence": "c", "question_a_trancher": "q"}


def lens_block(*pistes, objection="objection"):
    return {"pistes": list(pistes), "objection_principale": objection}


def all_lenses(**per_lens):
    out = {}
    for i, lens in enumerate(bs.LENSES):
        out[lens] = per_lens.get(lens.replace("-", "_"), lens_block(piste("P-%d" % i, "angle %s" % lens, lens=lens)))
    return out


class ArgumentsTest(unittest.TestCase):
    def test_bs01_subject_is_every_token_not_starting_with_dashes_word_for_word(self):
        """BS-01: SUBJECT = the tokens that do not start with '--', in order; options are taken out."""
        got = bs.parse_arguments("Réécrire l'API --rounds 2 en  Rust")
        self.assertEqual(got["subject"], "Réécrire l'API en Rust")
        self.assertEqual(bs.parse_arguments("--rounds 1 idée")["subject"], "idée")
        self.assertEqual(bs.parse_arguments("")["subject"], "")
        self.assertIsNone(bs.parse_arguments("--rounds 2")["path"])
        with self.assertRaises(bs.BsError):
            bs.parse_arguments("idée --round 2")

    def test_bs02_rounds_capped_defaulted_and_validated(self):
        """BS-02: --rounds min(N, 3), default 3; a non-integer or < 1 value is an explicit error."""
        self.assertEqual(bs.parse_arguments("x")["rounds"], 3)
        self.assertEqual(bs.parse_arguments("x --rounds 2")["rounds"], 2)
        self.assertEqual(bs.parse_arguments("x --rounds 9")["rounds"], 3)
        self.assertEqual(bs.parse_arguments("x --rounds=1")["rounds"], 1)
        for bad in ("x --rounds 0", "x --rounds abc", "x --rounds -2", "x --rounds 1.5", "x --rounds"):
            with self.assertRaises(bs.BsError, msg=bad):
                bs.parse_arguments(bad)

    def test_bs09_slug_and_path(self):
        """BS-09: lowercase, accents removed, non-alphanumeric -> '-', merged, <= 40, no trailing dash."""
        self.assertEqual(bs.slug("Réécrire l'API — v2 !"), "reecrire-l-api-v2")
        self.assertEqual(bs.synthesis_path("Réécrire l'API — v2 !"), "BRAINSTORM-reecrire-l-api-v2.md")
        long = bs.slug("a" * 39 + " bbbbbbbbbbbbbbbbbbbb")
        self.assertLessEqual(len(long), 40)
        self.assertFalse(long.endswith("-"))
        self.assertEqual(bs.slug("x" * 60), "x" * 40)
        self.assertEqual(bs.parse_arguments("Mon idée")["path"], "BRAINSTORM-mon-idee.md")


class LensTest(unittest.TestCase):
    def test_bs04_lens_schema(self):
        """BS-04: 4 angles at most, origine enonce|hors-enonce, objection required; text blocks are parsed."""
        self.assertEqual(len(bs.parse_lens("cout", lens_block(piste("A-1", "t")))["pistes"]), 1)
        text = "Analyse.\n" + json.dumps(lens_block(piste("A-1", "t")))
        self.assertEqual(bs.parse_lens("cout", text)["objection_principale"], "objection")
        for bad in (lens_block(*[piste("A-%d" % i, "t%d" % i) for i in range(5)]),
                    lens_block(dict(piste("A-1", "t"), origine="dehors")),
                    lens_block(piste("A-1", "t"), objection=""),
                    {"pistes": "x", "objection_principale": "o"}, "no block"):
            with self.assertRaises(verdicts.UnusableOutput):
                bs.parse_lens("cout", bad)
        with self.assertRaises(bs.BsError):
            bs.parse_lens("inconnue", lens_block())

    def test_bs03_registry_exact_key(self):
        """BS-03: an angle whose title is already discarded is removed; a reworded one is not (judgment)."""
        kept, removed = bs.filter_registry([piste("A", "Ne rien faire"), piste("B", "Ne  rien   faire du tout")],
                                           [{"titre": "Ne rien faire"}])
        self.assertEqual([p["id"] for p in removed], ["A"])
        self.assertEqual([p["id"] for p in kept], ["B"])


class RoundTest(unittest.TestCase):
    def test_bs05_dead_lens_relaunched_once_then_named_and_no_early_exit(self):
        """BS-05: a dead lens is relaunched once; dead twice with 0 'hors-enonce' angle -> no early exit."""
        enonce_only = {lens: lens_block(piste("E-%d" % i, "e%d" % i, origine="enonce", lens=lens))
                       for i, lens in enumerate(bs.LENSES)}
        blocks = dict(enonce_only, cout=None)
        first = bs.round_step(1, 3, blocks, [])
        self.assertEqual(first, {"action": "relaunch", "lenses": ["cout"]})
        second = bs.round_step(1, 3, blocks, [], relaunched=["cout"])
        self.assertEqual(second["action"], "synthesize")
        self.assertEqual(second["missing_lenses"], ["cout"])
        self.assertFalse(second["early_exit"])
        self.assertTrue(second["early_exit_disabled"])
        self.assertFalse(second["converge"])
        healthy = bs.round_step(1, 3, enonce_only, [])
        self.assertTrue(healthy["early_exit"])

    def test_bs05_unusable_output_counts_as_dead(self):
        """BS-05: an unusable block is treated like a dead agent; a missing lens key is an error."""
        blocks = all_lenses()
        blocks["hypotheses"] = "no json here"
        self.assertEqual(bs.round_step(1, 3, blocks, [])["lenses"], ["hypotheses"])
        del blocks["cout"]
        with self.assertRaises(bs.BsError):
            bs.round_step(1, 3, blocks, [])

    def test_bs06_early_exit_when_no_outside_angle_survives(self):
        """BS-06: the registry can remove the last 'hors-enonce' angle: early exit; one survivor -> none."""
        blocks = {lens: lens_block(piste("H-%d" % i, "same", lens=lens)) for i, lens in enumerate(bs.LENSES)}
        step = bs.round_step(1, 3, blocks, [{"titre": "same"}])
        self.assertTrue(step["early_exit"])
        self.assertEqual(len(step["removed"]), 5)
        self.assertTrue(step["converge"])
        self.assertFalse(step["ceiling"])
        blocks["cout"] = lens_block(piste("H-9", "fresh", lens="cout"))
        self.assertFalse(bs.round_step(1, 3, blocks, [{"titre": "same"}])["early_exit"])

    def test_bs07_ceiling_forces_convergence(self):
        """BS-07: at the round ceiling the convergence is forced, even with new angles."""
        step = bs.round_step(3, 3, all_lenses(), [])
        self.assertTrue(step["converge"])
        self.assertTrue(step["ceiling"])
        self.assertFalse(bs.round_step(2, 3, all_lenses(), [])["converge"])

    def test_bs04_each_round_carries_the_objections(self):
        """BS-04: the objections of the lenses are handed to the synthesis."""
        self.assertEqual(len(bs.round_step(1, 3, all_lenses(), [])["objections"]), 5)


def synthesis(**extra):
    data = {"subject": "Réécrire l'API — v2 !", "idea": "L'idée a bougé.", "objections": ["Le coût tient."],
            "ranked": [{"title": "Angle %d" % i, "marking": "hors-enonce" if i % 2 else "enonce",
                        "body": "thèse %d" % i} for i in range(1, 8)],
            "discarded": [{"title": "Écartée", "marking": "enonce", "round": 1, "reason": "hors sujet",
                           "by": "utilisateur"}],
            "played": 2, "rounds": 3, "end": "early", "missing_lenses": []}
    data.update(extra)
    return data


class SynthesisTest(helpers.TempDirCase):
    def test_bs08_cap_five_retained_surplus_with_rank(self):
        """BS-08: 7 surviving angles -> 5 retained; the 2 others are 'not retained' with their rank 6 and 7."""
        capped = bs.cap_retained(synthesis()["ranked"])
        self.assertEqual(len(capped["retained"]), 5)
        self.assertEqual([p["rank"] for p in capped["not_retained"]], [6, 7])
        text = bs.render(synthesis())
        self.assertEqual(text.count("**Angle "), 5)
        self.assertIn("| Angle 6 | 6 | beyond the cap of 5 |", text)
        self.assertIn("| Angle 7 | 7 |", text)
        self.assertNotIn("Angles not retained", bs.render(synthesis(ranked=synthesis()["ranked"][:3])))

    def test_bs11_render_sections_in_order(self):
        """BS-11: the synthesis follows the template order and shows markings and the end of session."""
        text = bs.render(synthesis())
        order = ["# Brainstorm — Réécrire", "## The idea after confrontation", "## Objections that held",
                 "## Retained angles (5 maximum)", "## Discarded angles", "## Angles not retained in this cycle",
                 "## End of session"]
        positions = [text.index(h) for h in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("`[outside the brief]`", text)
        self.assertIn("`[in the brief]`", text)
        self.assertIn("Rounds played: 2/3. End: early exit.", text)
        self.assertNotIn("Lenses not queried", text)
        self.assertIn("Lenses not queried (agent silent after a relaunch): cout.",
                      bs.render(synthesis(missing_lenses=["cout"])))
        with self.assertRaises(bs.BsError):
            bs.render(synthesis(end="because"))

    def test_bs10_resume_loads_discarded_and_rewrite_keeps_every_line(self):
        """BS-10: the 'Discarded angles' rows of an earlier file are loaded; the rewrite keeps all of them."""
        path = self.tmp / "BRAINSTORM-reecrire-l-api-v2.md"
        old = bs.render(synthesis(discarded=[
            {"title": "Ancienne piste", "marking": "enonce", "round": 1, "reason": "raison", "by": "lentille"},
            {"title": "Autre", "marking": "hors-enonce", "round": 2, "reason": "r2", "by": "utilisateur"}]))
        path.write_text(old, encoding="utf-8")
        loaded = bs.resume(self.tmp, path.name)
        self.assertTrue(loaded["exists"])
        self.assertEqual([d["titre"] for d in loaded["discarded"]], ["Ancienne piste", "Autre"])
        self.assertFalse(bs.resume(self.tmp, "BRAINSTORM-none.md")["exists"])
        new = bs.render(synthesis(), loaded["rows"])
        for row in loaded["rows"]:
            self.assertIn(row, new)
        self.assertEqual(new.count("| Écartée |"), 1)
        self.assertEqual(new, bs.render(synthesis(), loaded["rows"] + loaded["rows"]))


class FinishTest(helpers.TempDirCase):
    def finish(self, data):
        return helpers.harness(["bs", "finish", "--root", str(self.tmp)], self.tmp, stdin=json.dumps(data))

    def files(self):
        return sorted(p.relative_to(self.tmp).as_posix() for p in self.tmp.rglob("*") if p.is_file())

    def test_bs12_x05_nothing_written_before_the_final_gate(self):
        """BS-12, X5: args, round and render write nothing; finish writes the journal and the synthesis together."""
        proc = helpers.harness(["bs", "args"], self.tmp, stdin="Mon idée --rounds 2")
        self.assertEqual(json.loads(proc.stdout)["rounds"], 2)
        proc = helpers.harness(["bs", "round"], self.tmp, stdin=json.dumps(
            {"round": 1, "rounds": 3, "lenses": all_lenses(), "discarded": []}))
        self.assertEqual(json.loads(proc.stdout)["action"], "synthesize")
        proc = helpers.harness(["bs", "render", "--root", str(self.tmp)], self.tmp, stdin=json.dumps(synthesis()))
        self.assertIn("# Brainstorm", proc.stdout)
        self.assertEqual(self.files(), [])
        data = synthesis(write_answer="Write the synthesis", gates=[
            {"id": "brainstorm.subject", "answer": "Réécrire l'API — v2 !"},
            {"id": "brainstorm.round", "answer": "Dig / continue"}])
        done = self.finish(data)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertTrue(out["written"])
        self.assertEqual(out["path"], "BRAINSTORM-reecrire-l-api-v2.md")
        self.assertTrue((self.tmp / out["path"]).read_text(encoding="utf-8").startswith("# Brainstorm"))
        records = journal.read(Path(self.tmp) / out["run"])
        events = [(r["id"], r["phase"], r["decision"], r.get("answer")) for r in records]
        self.assertEqual(events[0][:3], ("brainstorm.subject", "decision", "ask"))
        self.assertEqual(events[-1][0:2], ("brainstorm.write", "answer"))
        self.assertEqual(events[-1][3], "Write the synthesis")
        valid = helpers.harness(["journal", "validate", out["run"]], self.tmp)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertRegex(Path(out["run"]).name, journal.NAME_RE)

    def test_bs12_refusal_writes_no_synthesis_but_the_journal(self):
        """BS-12: 'Write nothing' -> no BRAINSTORM file; the journal records the refusal."""
        done = self.finish(synthesis(write_answer="Write nothing"))
        out = json.loads(done.stdout)
        self.assertFalse(out["written"])
        self.assertEqual([f for f in self.files() if f.startswith("BRAINSTORM")], [])
        self.assertTrue(any(f.startswith(".harness/runs/brainstorm-") for f in self.files()))

    def test_bs12_dialogue_gates_always_ask_even_in_auto(self):
        """BS-12 (GT-04): the three brainstorm gates are dialogue gates: ask, whatever the policy."""
        for gate in ("brainstorm.subject", "brainstorm.round", "brainstorm.write"):
            self.assertTrue(gates.get(gate).dialogue)
            self.assertEqual(gates.decide(gate, "auto")["decision"], "ask")
        self.assertEqual(gates.get("brainstorm.write").default, "Write nothing")

    def test_bs12_headless_fallback_is_stop(self):
        """BS-12: no interaction possible -> stop for the three gates (the French stop message stays in the skill)."""
        for gate in ("brainstorm.subject", "brainstorm.round", "brainstorm.write"):
            self.assertEqual(gates.get(gate).headless, "stop")

    def test_bs10_finish_keeps_the_earlier_discarded_lines(self):
        """BS-10: finish over an existing file keeps every earlier discarded row."""
        first = self.finish(synthesis(write_answer="Write the synthesis", discarded=[
            {"title": "Ancienne", "marking": "enonce", "round": 1, "reason": "r", "by": "lentille"}]))
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.finish(synthesis(write_answer="Write the synthesis"))
        self.assertEqual(second.returncode, 0, second.stderr)
        text = (self.tmp / "BRAINSTORM-reecrire-l-api-v2.md").read_text(encoding="utf-8")
        self.assertIn("| Ancienne |", text)
        self.assertIn("| Écartée |", text)

    def test_bs11_forbidden_status_strings_are_refused(self):
        """BS-11 (FD-11): a synthesis holding 'Statut : APPROUVEE' is refused, nothing is written."""
        done = self.finish(synthesis(write_answer="Write the synthesis", idea="Statut : APPROUVEE"))
        self.assertEqual(done.returncode, 2)
        self.assertEqual([f for f in self.files() if f.startswith("BRAINSTORM")], [])

    def test_bs12_invalid_answer_is_refused(self):
        """BS-12: the write gate takes one of its two options."""
        self.assertEqual(self.finish(synthesis(write_answer="peut-être")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
