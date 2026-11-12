# Module hcore.tshirt and the 'tshirt' CLI: sizes, orderings, quick wins,
# finding -> spec mapping, load (TS-01 to TS-05, CA-21).

import json
import random
import unittest

import helpers
from hcore import tshirt


def f(fid, severity="major", size="M", axis="hygiene", **extra):
    item = {"id": fid, "source": "legacy-audit", "severity": severity, "axis": axis, "title": fid,
            "status": "CONFIRMED", "history": ["PROPOSED"], "size": size}
    item.update(extra)
    return item


class SizesTest(unittest.TestCase):
    def test_ts01_ordered_enumeration_and_scales(self):
        """TS-01: XS < S < M < L < XL; development and GPU scales."""
        self.assertEqual(tshirt.SIZES, ("XS", "S", "M", "L", "XL"))
        self.assertEqual([tshirt.size_rank(s) for s in tshirt.SIZES], [0, 1, 2, 3, 4])
        # Every level of both scales, as written at BASE1 (legacy-audit:50, ai-lab-audit:66).
        self.assertEqual(tshirt.DEV_SCALE, {"XS": "< 30 min", "S": "< 2 h", "M": "half a day",
                                            "L": "1-2 days", "XL": "to be split"})
        self.assertEqual(tshirt.GPU_SCALE, {"XS": "a few GPU minutes", "S": "< 1 h", "M": "half a day of runs",
                                            "L": "several days", "XL": "beyond the reach of the hardware"})
        self.assertEqual(list(tshirt.DEV_SCALE), list(tshirt.SIZES))
        # The labels are data (templates/deliverables.json), not code strings.
        source = (helpers.CORE / "hcore" / "tshirt.py").read_text(encoding="utf-8")
        self.assertNotIn("to be split", source)
        with self.assertRaises(tshirt.SizeError):
            tshirt.size_rank("XXL")


class OrderTest(unittest.TestCase):
    def test_ts03_ca21_leg_order_severity_then_size(self):
        """TS-03, CA-21: LEG (and COR, Q) = severity descending, then size ascending."""
        items = [f("a", "minor", "XS"), f("b", "major", "L"), f("c", "critical", "M"), f("d", "major", "S"),
                 f("e", "critical", "XS")]
        for order in ("LEG", "COR", "Q"):
            self.assertEqual([x["id"] for x in tshirt.ordered(items, order)], ["e", "c", "d", "b", "a"])

    def test_ts03_ca21_vib_critical_first_all_axes_and_regression_risk_ignored(self):
        """TS-03, CA-21: VIB puts a critical of axis evolutivite before any major
        securite; then securite > maintenabilite > evolutivite, then difficulty;
        regression_risk has no effect."""
        items = [f("sec-major", "major", "XS", "securite"), f("evo-crit", "critical", "XL", "evolutivite"),
                 f("mnt-major", "major", "XS", "maintenabilite"), f("sec-major-L", "major", "L", "securite"),
                 f("sec-crit", "critical", "S", "securite")]
        order = [x["id"] for x in tshirt.ordered(items, "VIB")]
        self.assertEqual(order, ["sec-crit", "evo-crit", "sec-major", "sec-major-L", "mnt-major"])
        self.assertLess(order.index("evo-crit"), order.index("sec-major"))
        self.assertEqual(tshirt.raised_criticals(items), ["evo-crit"])
        rng = random.Random(7)
        for _ in range(5):
            shaken = [dict(x, regression_risk=rng.choice(["faible", "moyen", "eleve"])) for x in items]
            self.assertEqual([x["id"] for x in tshirt.ordered(shaken, "VIB")], order)

    def test_ts04_quick_wins_flagged_without_moving(self):
        """TS-04: XS or S of severity major or critical, order unchanged."""
        items = tshirt.ordered([f("a", "critical", "XL"), f("b", "major", "S"), f("c", "minor", "XS"),
                                f("d", "critical", "XS")], "VIB")
        self.assertEqual(tshirt.quick_wins(items), ["d", "b"])
        self.assertEqual([x["id"] for x in items], ["d", "a", "b", "c"])


class SpecPlanTest(unittest.TestCase):
    def test_ts02_ca21_xl_split_in_at_least_two_chained_specs(self):
        """TS-02, CA-21: an XL becomes at least 2 specs <= L, chained."""
        specs = tshirt.plan_specs([f("x", size="XL"), f("y", size="M")], "category-milestone")
        self.assertEqual([(s["kind"], s["max_size"], s["depends_on"]) for s in specs],
                         [("part", "L", None), ("part", "L", 0), ("single", "M", None)])
        three = tshirt.plan_specs([f("x", size="XL")], "lens", parts={"x": 3})
        self.assertEqual([s["depends_on"] for s in three], [None, 0, 1])
        with self.assertRaises(tshirt.SizeError):
            tshirt.plan_specs([f("x", size="XL")], "lens", parts={"x": 1})

    def test_ts02_ca21_five_xs_same_category_and_milestone_give_two_specs(self):
        """TS-02, CA-21: 5 XS of one category and one milestone -> 2 specs (4 + 1);
        another milestone or category is never mixed; lens grouping for ai-lab-audit."""
        xs = [f("x%d" % i, size="XS", category="gitignore", milestone=1) for i in range(5)]
        specs = tshirt.plan_specs(xs, "category-milestone")
        self.assertEqual([s["findings"] for s in specs], [["x0", "x1", "x2", "x3"], ["x4"]])
        self.assertEqual([s["kind"] for s in specs], ["group", "single"])
        mixed = [f("a", size="XS", category="c", milestone=1), f("b", size="XS", category="c", milestone=2),
                 f("c", size="XS", category="d", milestone=1)]
        self.assertEqual(len(tshirt.plan_specs(mixed, "category-milestone")), 3)
        lens = [f("l%d" % i, size="XS", axis="fuites") for i in range(2)]
        self.assertEqual(tshirt.plan_specs(lens, "lens")[0]["findings"], ["l0", "l1"])


class LoadTest(helpers.TempDirCase):
    def test_ts05_load_per_milestone(self):
        """TS-05: count and size distribution per milestone and in total; CLI."""
        items = [f("a", size="XS", milestone=1), f("b", size="XS", milestone=1), f("c", size="M", milestone=1),
                 f("d", size="L", milestone=2)]
        load = tshirt.load(items)
        self.assertEqual(load["groups"]["1"], {"count": 3, "sizes": {"XS": 2, "M": 1}, "text": "2 XS, 1 M"})
        self.assertEqual(load["total"]["count"], 4)
        path = self.tmp / "f.json"
        path.write_text(json.dumps(items), encoding="utf-8")
        res = helpers.harness(["tshirt", "--file", str(path), "--order", "LEG", "--grouping",
                               "category-milestone", "--load-by", "milestone"], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["order"], ["a", "b", "c", "d"])
        self.assertEqual(out["load"]["groups"]["2"]["text"], "1 L")
        self.assertEqual(helpers.harness(["tshirt", "--file", str(path), "--order", "NOPE"], self.tmp).returncode, 2)

if __name__ == "__main__":
    unittest.main()
