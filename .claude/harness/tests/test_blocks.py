# Module hcore.blocks: delimited blocks (D9, BK-01).

import unittest

import helpers  # noqa: F401
from hcore import blocks
from hcore.blocks import BlockError


class BlocksTest(unittest.TestCase):
    def test_upsert_appends_then_replaces_in_place(self):
        text = "# Agent\n\nUser prompt.\n"
        once = blocks.upsert_block(text, "rgkb", "rule v1", "md")
        self.assertIn("<!-- rgkb:debut -->\nrule v1\n<!-- rgkb:fin -->", once)
        self.assertTrue(once.startswith(text))
        twice = blocks.upsert_block(once, "rgkb", "rule v2", "md")
        self.assertNotIn("rule v1", twice)
        self.assertEqual(twice.count("rgkb:debut"), 1)
        self.assertEqual(blocks.upsert_block(twice, "rgkb", "rule v2", "md"), twice)

    def test_hash_style_and_get(self):
        text = blocks.upsert_block("#!/bin/sh\n", "rgkb", "echo hi", "hash")
        self.assertIn("# >>> rgkb >>>", text)
        self.assertEqual(blocks.get_block(text, "rgkb", "hash"), "echo hi")
        self.assertIsNone(blocks.get_block("nothing", "rgkb", "hash"))

    def test_remove_restores_text(self):
        text = "start\n"
        self.assertEqual(blocks.remove_block(blocks.upsert_block(text, "b", "x", "md"), "b", "md"), text)

    def test_bk01_rgkb_wiring_block(self):
        """BK-01: the <!-- rgkb:debut --> / <!-- rgkb:fin --> wiring block, reentrant."""
        text = blocks.upsert_rgkb("# Agent\n\nPrompt.\n", "Use the index.")
        self.assertIn("<!-- rgkb:debut -->\nUse the index.\n<!-- rgkb:fin -->\n", text)
        again = blocks.upsert_rgkb(text, "Use the index first.")
        self.assertEqual(again.count("<!-- rgkb:debut -->"), 1)
        self.assertEqual(blocks.get_block(again, "rgkb", "md"), "Use the index first.")
        self.assertTrue(again.startswith("# Agent\n\nPrompt.\n"))

    def test_malformed_refused(self):
        with self.assertRaises(BlockError):
            blocks.upsert_block("<!-- b:debut -->\nno end\n", "b", "x", "md")
        with self.assertRaises(BlockError):
            blocks.markers("invalid name", "md")
        with self.assertRaises(BlockError):
            blocks.markers("b", "xml")


if __name__ == "__main__":
    unittest.main()
