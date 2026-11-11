# Module hcore.counters: counter bounds (SP-01, SP-02, FR-03, D8).

import unittest

import helpers
from hcore import counters
from hcore.counters import BoundsError


class RoundsTest(unittest.TestCase):
    def test_sp01_default_is_3(self):
        """SP-01."""
        self.assertEqual(counters.parse_rounds(None), 3)

    def test_sp01_hard_cap_min_n_3(self):
        """SP-01."""
        self.assertEqual(counters.parse_rounds("1"), 1)
        self.assertEqual(counters.parse_rounds("2"), 2)
        self.assertEqual(counters.parse_rounds("3"), 3)
        self.assertEqual(counters.parse_rounds("10"), 3)

    def test_sp01_invalid_is_explicit_error(self):
        """SP-01."""
        for bad in ("0", "-1", "two", "1.5", ""):
            with self.assertRaises(BoundsError):
                counters.parse_rounds(bad)


class ThemesTest(unittest.TestCase):
    def test_sp02_default_three_themes(self):
        """SP-02."""
        self.assertEqual(len(counters.parse_themes(None)), 3)

    def test_sp02_split_on_semicolon(self):
        """SP-02."""
        self.assertEqual(counters.parse_themes("logic; regressions"), ["logic", "regressions"])

    def test_sp02_four_max(self):
        """SP-02."""
        self.assertEqual(len(counters.parse_themes("a;b;c;d")), 4)
        with self.assertRaises(BoundsError):
            counters.parse_themes("a;b;c;d;e")

    def test_sp02_empty_list_is_explicit_error(self):
        """SP-02."""
        for bad in ("", " ", ";", " ; ; "):
            with self.assertRaises(BoundsError):
                counters.parse_themes(bad)


    def test_sp02_duplicate_theme_is_explicit_error_naming_it(self):
        """SP-02 / D8 (BACKLOG section 7): a theme given twice is an explicit
        error naming it, documented in the help of --themes."""
        with self.assertRaises(BoundsError) as ctx:
            counters.parse_themes("logic; regressions ;logic")
        self.assertIn("'logic'", str(ctx.exception))
        self.assertIn("give each theme once", str(ctx.exception))
        res = helpers.harness(["ship-pr", "start", "--help"], helpers.TESTS)
        self.assertEqual(res.returncode, 0)
        self.assertIn("theme given twice", " ".join(res.stdout.split()))


class CapTest(unittest.TestCase):
    def test_fr03_three_iterations(self):
        """FR-03."""
        self.assertEqual(counters.MAX_ITERATIONS, 3)
        self.assertFalse(counters.cap_reached(2, counters.MAX_ITERATIONS))
        self.assertTrue(counters.cap_reached(3, counters.MAX_ITERATIONS))


if __name__ == "__main__":
    unittest.main()
