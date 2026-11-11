# Module hcore.registry: cumulative registry and deduplication (SP-07, D2).

import json
import unittest

import helpers
from hcore import registry


class NormalizeTest(unittest.TestCase):
    def test_sp07_title_normalized(self):
        """SP-07."""
        self.assertEqual(registry.normalize_title("  Naïve  Bound Check !"), "naive bound check")
        self.assertEqual(registry.finding_key("./src\\a.py", "Off-by-one"),
                         registry.finding_key("src/a.py", "off by one"))

    def test_sp07_other_file_is_other_key(self):
        """SP-07."""
        self.assertNotEqual(registry.finding_key("a.py", "x"), registry.finding_key("b.py", "x"))


class RegistryFileTest(helpers.TempDirCase):
    def test_sp07_rejected_is_known_after_reload(self):
        """SP-07."""
        path = self.tmp / "ship-pr-20261016-120000.registry.jsonl"
        reg = registry.Registry(path)
        reg.add(helpers.finding(title="False positive"), "REJECTED", round=1)
        again = registry.Registry(path)
        key = registry.finding_key("src/a.py", "false positive")
        self.assertTrue(again.known(key))
        self.assertEqual(again.current(key), "REJECTED")

    def test_d2_line_format_and_last_status_wins(self):
        path = self.tmp / "r.registry.jsonl"
        reg = registry.Registry(path)
        reg.add(helpers.finding(), "CONFIRMED", round=1)
        reg.add(helpers.finding(), "FIXED", round=1, commit="abc")
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["verdict"] for x in lines], ["CONFIRMED", "FIXED"])
        for key in ("file", "line", "title", "verdict"):
            self.assertIn(key, lines[0])
        self.assertEqual(registry.Registry(path).current(lines[0]["key"]), "FIXED")

    def test_sp07_add_is_idempotent_even_after_reload(self):
        """SP-07: a relaunch after an interruption does not duplicate the registry."""
        path = self.tmp / "r.registry.jsonl"
        registry.Registry(path).add(helpers.finding(), "REJECTED", round=1, id="R1-F1", reason="r")
        registry.Registry(path).add(helpers.finding(), "REJECTED", round=1, id="R1-F1", reason="r")
        reg = registry.Registry(path)
        reg.add(helpers.finding(), "REJECTED", round=1, id="R1-F1", reason="r")
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
        reg.add(helpers.finding(), "UNVALIDATED", round=2, id="R2-F1")
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_d2_unknown_status_refused(self):
        with self.assertRaises(ValueError):
            registry.Registry(self.tmp / "x.jsonl").add(helpers.finding(), "MAYBE")


if __name__ == "__main__":
    unittest.main()
