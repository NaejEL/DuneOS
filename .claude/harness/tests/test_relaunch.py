# Module hcore.relaunch: one relaunch then explicit failure (FR-04, SP-05, SP-06).

import unittest

import helpers  # noqa: F401
from hcore import relaunch


class RelaunchTest(unittest.TestCase):
    def test_fr04_first_death_relaunches(self):
        """FR-04."""
        self.assertEqual(relaunch.on_dead(0), relaunch.RELAUNCH)

    def test_fr04_second_death_fails(self):
        """FR-04."""
        self.assertEqual(relaunch.on_dead(1), relaunch.FAIL)
        self.assertEqual(relaunch.on_dead(5), relaunch.FAIL)

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            relaunch.on_dead(-1)


if __name__ == "__main__":
    unittest.main()
