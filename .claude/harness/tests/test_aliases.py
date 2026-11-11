# Old audits kept as aliases of /audit (AX-04 to AX-06).

import json
import unittest

import helpers
from hcore import aliases, journal


def translate(skill, text):
    return aliases.translate(skill, text)["arguments"]


class TranslationTest(unittest.TestCase):
    def test_ax05_legacy_audit(self):
        """AX-05: legacy-audit [R] --categories X --max-specs N -> audit [R] --profile software --lenses
        <X or the whole set> --max-specs N --via legacy-audit; N bounded to 25."""
        self.assertEqual(translate("legacy-audit", '--categories "hygiene;qualite"'),
                         '--profile software --lenses "hygiene;qualite" --via legacy-audit')
        self.assertEqual(translate("legacy-audit", ""),
                         '--profile software --lenses "hygiene;portabilite;qualite;filet" --via legacy-audit')
        self.assertEqual(translate("legacy-audit", "--max-specs 99"),
                         '--profile software --lenses "hygiene;portabilite;qualite;filet" --max-specs 25 '
                         '--via legacy-audit')

    def test_ax05_vibecode_audit_expands_the_axes_into_lenses(self):
        """AX-05: vibecode-audit --axes X -> the lenses of the axes, or the whole vibecode set."""
        self.assertEqual(translate("vibecode-audit", '--axes "maintenabilite"'),
                         '--profile software --lenses maintenabilite --via vibecode-audit')
        self.assertEqual(translate("vibecode-audit", '--axes "securite;evolutivite"'),
                         '--profile software --lenses "acces;injection;metier;evolutivite" --via vibecode-audit')
        self.assertIn("--max-findings 30", translate("vibecode-audit", "--max-findings 31"))

    def test_ax05_ai_lab_audit_keeps_lenses_questions_and_short_model_aliases(self):
        """AX-05, MD-06: ai-lab-audit forces research, keeps --lenses, --max-questions and the short model
        aliases; a path with spaces stays one token."""
        self.assertEqual(translate("ai-lab-audit", '"my repo" --lenses "validite" --max-questions 50 '
                                                   '--models "math=fable"'),
                         '"my repo" --profile research --lenses validite --max-questions 12 --models math=fable '
                         '--via ai-lab-audit')

    def test_ax06_the_profile_is_forced_and_bad_input_is_exit_2(self):
        """AX-06: every alias forces its profile (--profile before --via); an unknown option, lens or
        a cap below 1 is an error."""
        for skill, profile in (("legacy-audit", "software"), ("vibecode-audit", "software"),
                               ("ai-lab-audit", "research")):
            self.assertEqual(aliases.translate(skill, "")["profile"], profile)
        for skill, bad in (("legacy-audit", "--categories nope"), ("legacy-audit", "--max-specs 0"),
                           ("vibecode-audit", "--lenses acces"), ("ai-lab-audit", "--unknown x")):
            with self.assertRaises(aliases.AliasError, msg=bad):
                aliases.translate(skill, bad)


class AliasCliTest(helpers.TempDirCase):
    def test_ax04_alias_args_reads_a_file_written_by_write_and_the_journal_keeps_the_alias_event(self):
        """AX-04, CA-24: `audit alias-args` reads the file (consumed); `audit open --via` opens the
        audit-<timestamp> journal with an `alias` event that validates."""
        src = helpers.write_output(self.tmp, "legacy-audit-arguments.txt", 'repo --categories "filet"')
        res = helpers.harness(["audit", "alias-args", "--skill", "legacy-audit", "--root", str(self.tmp),
                               "--arguments-file", src], self.tmp)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["arguments"], 'repo --profile software --lenses filet --via legacy-audit')
        self.assertFalse((self.tmp / src).exists())
        opened = helpers.harness(["audit", "open", "--root", str(self.tmp), "--via", "legacy-audit"], self.tmp)
        run = self.tmp / json.loads(opened.stdout)["run"]
        self.assertRegex(run.name, r"^audit-\d{8}-\d{6}\.jsonl$")
        self.assertEqual(json.loads(run.read_text(encoding="utf-8").splitlines()[0])["event"], "alias")
        self.assertEqual(journal.validate_file(run), [])


if __name__ == "__main__":
    unittest.main()
