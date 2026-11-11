# Module hcore.verdicts: terminal JSON blocks (FR-02, FR-04, SP-03, SP-04).

import json
import unittest

import helpers
from hcore import verdicts
from hcore.verdicts import UnusableOutput


class TerminalJsonTest(unittest.TestCase):
    def test_bare_block_at_end(self):
        self.assertEqual(verdicts.extract_terminal_json('text {"a": 1}\n'), {"a": 1})

    def test_fenced_block_at_end(self):
        self.assertEqual(verdicts.extract_terminal_json('ok\n```json\n{"a": {"b": 2}}\n```'), {"a": {"b": 2}})

    def test_braces_in_prose_before_block(self):
        text = 'use {x} then {"verdict": "REJECTED", "reason": "r"}'
        self.assertEqual(verdicts.extract_terminal_json(text)["verdict"], "REJECTED")

    def test_text_after_block_is_unusable(self):
        with self.assertRaises(UnusableOutput):
            verdicts.extract_terminal_json('{"a": 1}\nthere')

    def test_absent_or_empty_is_unusable(self):
        for text in (None, "", "   ", "no json"):
            with self.assertRaises(UnusableOutput):
                verdicts.extract_terminal_json(text)


class VerifierBlockTest(unittest.TestCase):
    def test_fr02_fields_parsed(self):
        """FR-02."""
        out = verdicts.parse_verifier(helpers.verifier_block(
            "CHANGES_REQUESTED", False, [{"file": "a.py", "severity": "major", "description": "d"}]))
        self.assertEqual(out["verdict"], "CHANGES_REQUESTED")
        self.assertIs(out["tests_passed"], False)
        self.assertEqual(out["issues"][0]["file"], "a.py")

    def test_fr04_invalid_verdict_is_unusable(self):
        """FR-04."""
        for bad in ({"verdict": "OK", "tests_passed": True, "issues": []},
                    {"verdict": "APPROVED", "tests_passed": "true", "issues": []},
                    {"verdict": "APPROVED", "tests_passed": True, "issues": [{"severity": "huge", "description": "d"}]}):
            with self.assertRaises(UnusableOutput):
                verdicts.parse_verifier(json.dumps(bad))


class ReviewerBlockTest(unittest.TestCase):
    def test_sp03_findings_parsed(self):
        """SP-03."""
        out = verdicts.parse_reviewer(helpers.reviewer_block([helpers.finding()]))
        self.assertEqual(out[0]["line"], 3)
        self.assertEqual(out[0]["side"], "RIGHT")

    def test_sp03_zero_finding_is_valid(self):
        """SP-03."""
        self.assertEqual(verdicts.parse_reviewer('{"findings": []}'), [])

    def test_sp03_malformed_is_unusable(self):
        """SP-03."""
        bad_findings = [dict(helpers.finding(), line="3"), dict(helpers.finding(), title=""),
                        dict(helpers.finding(), severity="nit")]
        for bad in bad_findings:
            with self.assertRaises(UnusableOutput):
                verdicts.parse_reviewer(json.dumps({"findings": [bad]}))
        with self.assertRaises(UnusableOutput):
            verdicts.parse_reviewer('{"result": []}')


class ValidatorBlockTest(unittest.TestCase):
    def test_sp04_confirmed_and_rejected(self):
        """SP-04."""
        self.assertEqual(verdicts.parse_validator(helpers.validator_block("CONFIRMED"))["verdict"], "CONFIRMED")
        self.assertEqual(verdicts.parse_validator(helpers.validator_block("REJECTED", "nitpick"))["reason"], "nitpick")

    def test_sp04_invalid_is_unusable(self):
        """SP-04."""
        for bad in ('{"verdict": "MAYBE", "reason": "r"}', '{"verdict": "CONFIRMED"}', "nothing"):
            with self.assertRaises(UnusableOutput):
                verdicts.parse_validator(bad)


if __name__ == "__main__":
    unittest.main()
