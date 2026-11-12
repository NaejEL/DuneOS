# Module hcore.models and its wiring: closed roles, models.json, --models,
# migration of lab-models.json, presets, model block of generated agents
# (MD-01 to MD-08, X1, X6, X8, CA-18, CA-19).

import json
import unittest

import helpers
from hcore import fusion, journal, models
from hcore.models import ModelsError

AGENT = ("---\nname: factory-verifier\ndescription: Verifies.\ntools: Read, Glob, Grep, Bash\n---\n\n"
         "You verify `$ARGUMENTS` and $(date).\n")


def check_ok(test, res):
    test.assertEqual(res.returncode, 0, "stdout=%s\nstderr=%s" % (res.stdout, res.stderr))
    return json.loads(res.stdout)


class RolesTest(helpers.TempDirCase):
    def write(self, name, data):
        path = self.tmp / ".claude" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def test_md01_closed_role_set(self):
        """MD-01: closed role set; an unknown role is an explicit error."""
        self.assertEqual(models.ROLES, (
            "factory.planner", "factory.builder", "factory.verifier", "shippr.reviewer", "shippr.validator",
            "audit.auditor", "audit.validator", "lab.director.cadrage", "lab.director.interpretation",
            "lab.neuro", "lab.math", "lab.builder", "lab.verifier", "lab.validator", "harness.scout"))
        self.write("models.json", {"builder": "sonnet"})
        with self.assertRaises(ModelsError):
            models.load(self.tmp / ".claude" / "models.json", models.FACTORY_ROLES)
        with self.assertRaises(ModelsError):
            models.roles_of("factory,boss")
        self.assertEqual(models.roles_of("shippr"), models.SHIPPR_ROLES)

    def test_md02_ca18_absent_file_means_inherit_and_no_param(self):
        """MD-02, CA-18: missing file -> inherit everywhere, nothing written."""
        res = models.resolve_root(self.tmp, models.FACTORY_ROLES)
        self.assertEqual(res["roles"], {r: {"model": "inherit", "pass_param": False} for r in models.FACTORY_ROLES})
        self.assertFalse((self.tmp / ".claude").exists())

    def test_md03_x8_unknown_value_in_file_is_an_explicit_error(self):
        """MD-03 is replaced by X8 (decision of 2026-10-17): an unknown value in the
        file is refused, exit 2, instead of 'inherit with a warning'."""
        self.write("models.json", {"factory.builder": "gpt-4"})
        with self.assertRaises(ModelsError):
            models.resolve_root(self.tmp, models.FACTORY_ROLES)
        res = helpers.harness(["models", "resolve", "--roles", "factory"], self.tmp)
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown model", res.stderr)
        self.write("models.json", "{")
        self.assertEqual(helpers.harness(["models", "resolve"], self.tmp).returncode, 2)

    def test_md04_override_for_this_cycle_never_rewrites_the_file(self):
        """MD-04: --models for the cycle only; unknown role or value -> exit 2."""
        path = self.write("models.json", {"factory.builder": "sonnet", "lab.math": "fable"})
        before = path.read_bytes()
        res = models.resolve_root(self.tmp, models.FACTORY_ROLES, "factory.verifier=opus")
        self.assertEqual({r: e["model"] for r, e in res["roles"].items()},
                         {"factory.planner": "inherit", "factory.builder": "sonnet", "factory.verifier": "opus"})
        self.assertEqual(path.read_bytes(), before)
        for bad in ("factory.builder=gpt", "boss=opus", "factory.builder", "shippr.reviewer=haiku", ";"):
            with self.assertRaises(ModelsError):
                models.parse_override(bad, models.FACTORY_ROLES)
            res = helpers.harness(["models", "resolve", "--roles", "factory", "--models", bad], self.tmp)
            self.assertEqual(res.returncode, 2, bad)
        self.assertEqual(models.check_model("claude-opus-5-5"), "claude-opus-5-5")

    def test_md05_ca18_migration_once_and_never_destructive(self):
        """MD-05, CA-18: lab-models.json migrated once, left byte for byte."""
        legacy = self.write("lab-models.json", {"math": "fable", "validator": "haiku"})
        before = legacy.read_bytes()
        first = check_ok(self, helpers.harness(["models", "resolve", "--roles", "lab"], self.tmp))
        self.assertTrue(first["migrated"])
        self.assertEqual(first["roles"]["lab.math"], {"model": "fable", "pass_param": True})
        target = self.tmp / ".claude" / "models.json"
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                         {"lab.math": "fable", "lab.validator": "haiku"})
        self.assertEqual(legacy.read_bytes(), before)
        written = target.stat().st_mtime_ns, target.read_bytes()
        second = check_ok(self, helpers.harness(["models", "resolve", "--roles", "lab"], self.tmp))
        self.assertFalse(second["migrated"])
        self.assertIn("ignored", second["warnings"][0])
        self.assertEqual((target.stat().st_mtime_ns, target.read_bytes()), written)
        self.assertEqual(legacy.read_bytes(), before)

    def test_md05_invalid_legacy_file_writes_nothing(self):
        """MD-05: an invalid lab-models.json migrates nothing."""
        self.write("lab-models.json", {"math": "gpt"})
        with self.assertRaises(ModelsError):
            models.migrate(self.tmp)
        self.assertFalse((self.tmp / ".claude" / "models.json").exists())

    def test_md06_ca18_lab_aliases(self):
        """MD-06, CA-18: short lab aliases (math=fable) accepted with aliases='lab'."""
        self.assertEqual(models.parse_override("math=fable;validator=haiku;director.cadrage=opus",
                                               models.LAB_ROLES, aliases="lab"),
                         {"lab.math": "fable", "lab.validator": "haiku", "lab.director.cadrage": "opus"})
        with self.assertRaises(ModelsError):
            models.parse_override("math=fable", models.LAB_ROLES)
        res = check_ok(self, helpers.harness(["models", "resolve", "--roles", "lab", "--aliases", "lab",
                                              "--models", "math=fable"], self.tmp))
        self.assertEqual(res["roles"]["lab.math"]["model"], "fable")

    def test_md07_pass_param_only_when_not_inherit(self):
        """MD-07: inherit -> pass_param false, the Agent parameter is not passed."""
        self.assertEqual(models.entry("inherit"), {"model": "inherit", "pass_param": False})
        self.assertEqual(models.entry("haiku"), {"model": "haiku", "pass_param": True})

    def test_md08_presets_merge_without_overwriting(self):
        """MD-08: presets as data; writing completes missing keys, keeps existing ones."""
        for name in ("lab-inherit", "lab-experts", "lab-interpretation", "lab-custom", "economy", "quality"):
            self.assertIn(name, models.PRESETS)
        self.assertEqual(models.PRESETS["lab-experts"]["lab.neuro"], "fable")
        self.assertEqual(models.PRESETS["lab-interpretation"]["lab.director.interpretation"], "fable")
        self.assertEqual(models.PRESETS["lab-interpretation"]["lab.director.cadrage"], "inherit")
        path = self.write("models.json", {"lab.math": "haiku", "factory.builder": "opus"})
        out = check_ok(self, helpers.harness(["models", "write", "--preset", "lab-experts"], self.tmp))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["lab.math"], "haiku")
        self.assertEqual(data["lab.neuro"], "fable")
        self.assertEqual(data["factory.builder"], "opus")
        self.assertNotIn("lab.math", out["added"])
        before = path.read_bytes()
        again = check_ok(self, helpers.harness(["models", "write", "--preset", "lab-experts"], self.tmp))
        self.assertFalse(again["changed"])
        self.assertEqual(path.read_bytes(), before)
        explicit = check_ok(self, helpers.harness(["models", "write", "--preset", "economy", "--set",
                                                   "lab.math=fable"], self.tmp))
        self.assertEqual(explicit["models"]["lab.math"], "fable")
        self.assertEqual(explicit["models"]["factory.builder"], "opus")
        self.assertEqual(explicit["models"]["audit.validator"], "haiku")
        self.assertEqual(helpers.harness(["models", "write", "--preset", "nope"], self.tmp).returncode, 2)

    def test_x6_extract_option_keeps_the_need_verbatim(self):
        """X6: --models taken out of $ARGUMENTS, the need kept verbatim."""
        rest, value = models.extract_option("Réécrire l'API \"v2\" --models \"factory.builder=opus;factory.verifier=haiku\"")
        self.assertEqual(rest, "Réécrire l'API \"v2\"")
        self.assertEqual(value, "factory.builder=opus;factory.verifier=haiku")
        self.assertEqual(models.extract_option("l'API --models=factory.builder=opus"),
                         ("l'API", "factory.builder=opus"))
        self.assertEqual(models.extract_option("  l'API sans option "), ("l'API sans option", None))
        with self.assertRaises(ModelsError):
            models.extract_option("--models a=b --models c=d")
        rest, value = models.split_arguments('"my question" --models "math=fable;builder=sonnet"')
        self.assertEqual((rest, value), ("my question", "math=fable;builder=sonnet"))


class ModelBlockTest(helpers.TempDirCase):
    def test_x1_block_written_in_frontmatter_and_regenerated(self):
        """MD-09 is replaced by X1 (decision of 2026-10-17): model: is written in a
        delimited, regenerable block of the frontmatter of generated agents."""
        text = models.with_model_block(AGENT, "haiku")
        self.assertIn("tools: Read, Glob, Grep, Bash\n# >>> harness.model >>>\nmodel: haiku\n"
                      "# <<< harness.model <<<\n---\n", text)
        self.assertEqual(models.model_in_block(text), "haiku")
        self.assertEqual(models.with_model_block(text, "haiku"), text)
        self.assertEqual(models.model_in_block(models.with_model_block(text, "opus")), "opus")
        self.assertEqual(models.without_model_block(text), AGENT)
        crlf = AGENT.replace("\n", "\r\n")
        self.assertIn("model: haiku\r\n# <<< harness.model <<<\r\n---", models.with_model_block(crlf, "haiku"))
        with self.assertRaises(ModelsError):
            models.with_model_block("no frontmatter\n", "haiku")
        with self.assertRaises(ModelsError):
            models.with_model_block(AGENT.replace("tools:", "model: opus\ntools:"), "haiku")
        with self.assertRaises(ModelsError):
            models.with_model_block(AGENT, "gpt")

    def test_x1_install_agent_states(self):
        dest = self.tmp / ".claude" / "agents" / "factory-verifier.md"
        self.assertEqual(fusion.install_agent(dest, AGENT, "inherit"), {"state": "créé", "model_block": "written"})
        self.assertEqual(models.model_in_block(dest.read_text(encoding="utf-8")), "inherit")
        self.assertEqual(fusion.install_agent(dest, AGENT, "inherit")["state"], "identique")
        # models.json changed: only the block is regenerated.
        self.assertEqual(fusion.install_agent(dest, AGENT, "haiku"), {"state": "fusionné", "model_block": "updated"})
        self.assertEqual(dest.read_text(encoding="utf-8"), models.with_model_block(AGENT, "haiku"))
        # Customized outside the block: its text kept, its block regenerated, hence
        # a state of its own (the file was written).
        dest.write_text(dest.read_text(encoding="utf-8") + "My own rule.\n", encoding="utf-8")
        self.assertEqual(fusion.install_agent(dest, AGENT, "opus"),
                         {"state": "bloc-mis-à-jour", "model_block": "updated"})
        self.assertIn("My own rule.", dest.read_text(encoding="utf-8"))
        self.assertEqual(models.model_in_block(dest.read_text(encoding="utf-8")), "opus")
        # Customized and block already up to date: nothing written, customized-kept.
        written = dest.read_bytes()
        self.assertEqual(fusion.install_agent(dest, AGENT, "opus"),
                         {"state": "personnalisé-conservé", "model_block": "unchanged"})
        self.assertEqual(dest.read_bytes(), written)
        # Generated before X1 (no block): left untouched.
        dest.write_text(AGENT, encoding="utf-8")
        self.assertEqual(fusion.install_agent(dest, AGENT, "opus"),
                         {"state": "personnalisé-conservé", "model_block": "absent"})
        self.assertEqual(dest.read_text(encoding="utf-8"), AGENT)

    def test_x1_cli_install_with_model_role_reads_models_json(self):
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "models.json").write_text('{"factory.verifier": "haiku"}', encoding="utf-8")
        src = helpers.write_output(self.tmp, "factory-verifier.md", AGENT)
        res = check_ok(self, helpers.harness(["sf", "install", "--dest", ".claude/agents/factory-verifier.md",
                                              "--source", src, "--model-role", "factory.verifier"], self.tmp))
        self.assertEqual((res[".claude/agents/factory-verifier.md"], res["model"]), ("créé", "haiku"))
        self.assertFalse((self.tmp / src).exists())
        installed = (self.tmp / ".claude" / "agents" / "factory-verifier.md").read_text(encoding="utf-8")
        self.assertEqual(models.model_in_block(installed), "haiku")


class WiringTest(helpers.TempDirCase):
    def test_ca19_factory_run_verifier_logged_with_its_model(self):
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "models.json").write_text('{"factory.verifier": "haiku"}', encoding="utf-8")
        start = check_ok(self, helpers.harness(["factory-run", "start"], self.tmp))
        self.assertEqual(start["models"]["factory.verifier"], {"model": "haiku", "pass_param": True})
        self.assertEqual([r for r, e in start["models"].items() if e["pass_param"]], ["factory.verifier"])
        common = ["factory-run", "record", "--run", start["run"], "--duration-ms", "5"]
        check_ok(self, helpers.harness(common + ["--agent", "factory-builder"], self.tmp))
        out = helpers.write_output(self.tmp, "verifier-1.md", helpers.verifier_block("APPROVED", True))
        check_ok(self, helpers.harness(common + ["--agent", "factory-verifier", "--output", out], self.tmp))
        lines = [x for x in journal.read(self.tmp / start["run"]) if "event" not in x]     # + the autonomy event (AP-03)
        self.assertEqual([(x["agent"], x["model"]) for x in lines],
                         [("factory-builder", "inherit"), ("factory-verifier", "haiku")])

    def test_x6_factory_run_models_option_for_this_cycle(self):
        args = self.tmp / "args.txt"
        args.write_text("Réécrire l'API --models \"factory.builder=opus\"\n", encoding="utf-8")
        start = check_ok(self, helpers.harness(["factory-run", "start", "--arguments-file", str(args)], self.tmp))
        self.assertEqual((start["mode"], start["need"]), ("plan", "Réécrire l'API"))
        self.assertEqual(start["models"]["factory.builder"], {"model": "opus", "pass_param": True})
        self.assertFalse((self.tmp / ".claude" / "models.json").exists())
        args.write_text("x --models \"factory.builder=gpt\"", encoding="utf-8")
        res = helpers.harness(["factory-run", "start", "--arguments-file", str(args)], self.tmp)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(len(list((self.tmp / ".harness" / "runs").glob("*.jsonl"))), 1)


class VerifierDefectsTest(helpers.TempDirCase):
    """Minor defects found by the Verifier on L2 and L3."""

    def test_x6_a_need_that_merely_mentions_models_stays_verbatim(self):
        """X6, defect 3: only '--models role=model[;...]' at the head or the tail of
        $ARGUMENTS is the option; any other mention stays in the need, word for word."""
        for need in ("x --models", "Add a --models option to the CLI", "x --models notapair",
                     "Document --models \"factory.builder=opus\" in the README please"):
            self.assertEqual(models.extract_option(need), (need, None), need)
        self.assertEqual(models.extract_option('--models "factory.builder=opus" fix the --models parser'),
                         ("fix the --models parser", "factory.builder=opus"))
        self.assertEqual(models.extract_option("fix --models parsing --models factory.verifier=haiku;"),
                         ("fix --models parsing", "factory.verifier=haiku;"))
        args = self.tmp / "args.txt"
        args.write_text("Add a --models option to the CLI\n", encoding="utf-8")
        start = check_ok(self, helpers.harness(["factory-run", "start", "--arguments-file", str(args)], self.tmp))
        self.assertEqual((start["mode"], start["need"]), ("plan", "Add a --models option to the CLI"))

    def test_md04_role_given_twice_is_an_explicit_error(self):
        """MD-04, defect 4: a role given twice in --models is refused (exit 2), also
        through an alias, instead of silently keeping the last value."""
        with self.assertRaises(ModelsError):
            models.parse_override("factory.builder=opus;factory.builder=haiku", models.FACTORY_ROLES)
        with self.assertRaises(ModelsError):
            models.parse_override("math=fable;lab.math=haiku", models.LAB_ROLES, aliases="lab")
        res = helpers.harness(["models", "resolve", "--roles", "factory", "--models",
                               "factory.builder=opus;factory.builder=haiku"], self.tmp)
        self.assertEqual(res.returncode, 2)
        self.assertIn("twice", res.stderr)


if __name__ == "__main__":
    unittest.main()
