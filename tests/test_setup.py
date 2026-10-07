import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from helpers import FakeEnv
import test_cli
from roles import cli, config, presets, setup

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")


def scripted(answers):
    it = iter(answers)
    return lambda _prompt="": next(it)


MANUAL = ""     # first question: Enter = "직접 정하기" (the last option)

# manual -> planner(claude, with prompt) -> add more -> builder(shell command) -> no more -> lead planner -> team "mini" -> save
TWO_ROLES = [MANUAL, "planner", "", "1", "1", "설계를 맡습니다", "y",
             "builder", "Builder", "2", "pnpm dev", "n",
             "1", "mini", "y"]

PRESET_NUMBER = {pid: str(i) for i, pid in enumerate(presets.PRESETS, 1)}
# agent question options: 1 recommended (default), 2 pick per role, 3 all claude, 4 all codex
AGENTS_RECOMMENDED, AGENTS_EACH, ALL_CLAUDE, ALL_CODEX = "", "2", "3", "4"
# toss-silo -> recommended agents -> recommended models -> no extra role -> lead default -> team default -> save
# model question options: 1 recommended (default), 2 CLI default, 3 one per agent, 4 pick per role
MODELS_RECOMMENDED, MODELS_DEFAULT, MODELS_PER_AGENT, MODELS_EACH = "", "2", "3", "4"
TOSS_DEFAULTS = [PRESET_NUMBER["toss-silo"], AGENTS_RECOMMENDED, MODELS_RECOMMENDED, "", "", "", "y"]


def fake_codex_home(test, listed=("gpt-a", "gpt-b")):
    """Point CODEX_HOME at a temp dir whose model catalog lists `listed` in that order (and hides gpt-hidden)."""
    home = os.path.join(test.tmp, "codex")
    os.makedirs(home, exist_ok=True)
    models = [{"slug": "gpt-hidden", "visibility": "hide", "priority": 0}]
    models += [{"slug": slug, "visibility": "list", "priority": i} for i, slug in reversed(list(enumerate(listed, 1)))]
    with open(os.path.join(home, "models_cache.json"), "w") as f:
        json.dump({"models": models}, f)
    test._set_env("CODEX_HOME", home)


RECOMMENDED_CODEX = sorted({tier["codex"] for tier in (presets.LEAD, presets.RIGOR, presets.CORE, presets.LIGHT)})


class WizardTests(FakeEnv):
    def empty_config(self):
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)

    def test_needs_setup_only_when_no_roles_exist(self):
        self.assertFalse(setup.needs_setup())
        self.empty_config()
        self.assertTrue(setup.needs_setup())
        with open(os.path.join(self.cfg, "roles.toml"), "w") as f:
            f.write("# nothing here\n")
        self.assertTrue(setup.needs_setup())

    def test_malformed_roles_file_is_not_treated_as_empty(self):
        with open(os.path.join(self.cfg, "roles.toml"), "w") as f:
            f.write("[roles.x\n")
        self.assertFalse(setup.needs_setup())      # must surface the error, not get overwritten by the wizard

    def test_wizard_writes_loadable_roles_team_and_default(self):
        self.empty_config()
        team = setup.run_wizard(ask=scripted(TWO_ROLES), say=lambda *_: None)
        self.assertEqual(team, "mini")
        roles = config.load_roles()
        self.assertEqual(roles["planner"].agent, "claude")
        self.assertEqual(roles["planner"].prompt, "설계를 맡습니다")
        self.assertEqual(roles["builder"].command, "pnpm dev")
        self.assertIsNone(roles["builder"].agent)
        loaded = config.load_team("mini", roles)
        self.assertEqual(loaded.lead.role, "planner")
        self.assertEqual([m.role for m in loaded.members], ["planner", "builder"])
        self.assertEqual(config.load_settings()["default_team"], "mini")

    def test_garbled_answer_is_asked_again(self):
        self.empty_config()
        said = []
        answers = TWO_ROLES[:5] + ["설계를 �"] + TWO_ROLES[5:]
        setup.run_wizard(ask=scripted(answers), say=said.append)
        self.assertEqual(config.load_roles()["planner"].prompt, "설계를 맡습니다")
        self.assertTrue(any("깨진 글자" in s for s in said))

    def test_half_erased_hangul_bytes_do_not_crash_the_cli(self):
        # a tty without line editing erases one byte per backspace: "한" (ed 95 9c) minus one byte = ed 95
        self.empty_config()
        lines = [a.encode() for a in TWO_ROLES]
        lines.insert(5, "설계".encode() + b"\xed\x95")
        bin_roles = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "roles")
        out = subprocess.run([sys.executable, bin_roles, "setup"], input=b"\n".join(lines) + b"\n",
                             capture_output=True, env=os.environ.copy())
        self.assertEqual(out.returncode, 0, out.stderr.decode(errors="replace"))
        self.assertIn("깨진 글자", out.stdout.decode())
        self.assertEqual(config.load_roles()["planner"].prompt, "설계를 맡습니다")

    def test_no_input_ends_with_a_readable_message(self):
        # e.g. an agent running `roles setup` with nothing on stdin
        self.empty_config()
        bin_roles = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "roles")
        out = subprocess.run([sys.executable, bin_roles, "setup"], input=b"", capture_output=True, env=os.environ.copy())
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("입력이 끝나 설정을 중단했습니다", out.stderr.decode())
        self.assertNotIn("EOF when reading", out.stderr.decode())
        self.assertTrue(setup.needs_setup())

    def test_special_characters_survive_round_trip(self):
        self.empty_config()
        prompt = 'say "hi" \\ done'
        setup.run_wizard(ask=scripted([MANUAL, "r", "", "1", "1", prompt, "n", "t", "y"]), say=lambda *_: None)
        self.assertEqual(config.load_roles()["r"].prompt, prompt)

    def test_invalid_and_duplicate_answers_are_asked_again(self):
        self.empty_config()
        said = []
        # "Bad Name" rejected; second "dup" rejected as duplicate; lead choice "9" rejected
        answers = [MANUAL, "Bad Name", "dup", "", "3", "y", "dup", "other", "", "3", "n", "9", "1", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=said.append)
        self.assertEqual(sorted(config.load_roles()), ["dup", "other"])
        self.assertTrue(any("소문자" in s for s in said))
        self.assertTrue(any("이미 추가" in s for s in said))
        self.assertTrue(any("목록에 있는 번호" in s for s in said))

    def test_declining_confirmation_writes_nothing(self):
        self.empty_config()
        with self.assertRaises(setup.SetupAborted):
            setup.run_wizard(ask=scripted(TWO_ROLES[:-1] + ["n"]), say=lambda *_: None)
        self.assertEqual(os.listdir(self.cfg), [])

    def test_default_team_that_still_loads_with_the_new_roles_is_kept(self):
        # a team made only of `planner`, which TWO_ROLES defines again
        with open(os.path.join(self.cfg, "teams", "solo.toml"), "w") as f:
            f.write('[team]\nname = "solo"\n\n[[member]]\nrole = "planner"\nlead = true\n')
        path = os.path.join(self.cfg, "config.toml")
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace('default_team = "dev"', 'default_team = "solo"'))
        said = []
        setup.run_wizard(ask=scripted(TWO_ROLES), say=said.append)
        self.assertEqual(config.load_settings()["default_team"], "solo")
        self.assertFalse(any("기본 팀을" in s for s in said))

    def test_default_team_broken_by_the_new_roles_is_repointed(self):
        # `setup --force` over the examples: dev's implementer/reviewer/... are gone from the new roles.toml
        said = []
        setup.run_wizard(ask=scripted(TWO_ROLES), say=said.append)
        s = config.load_settings()
        self.assertEqual(s["default_team"], "mini")
        self.assertEqual(s["default_workflow"], "feature")          # other settings untouched
        self.assertTrue(any("'dev' → 'mini'" in line for line in said))
        self.assertTrue(any("기존 roles.toml 을 덮어씁니다" in line for line in said))

    def test_stale_default_team_is_repointed_and_other_settings_kept(self):
        os.remove(os.path.join(self.cfg, "roles.toml"))
        os.remove(os.path.join(self.cfg, "teams", "dev.toml"))
        setup.run_wizard(ask=scripted(TWO_ROLES), say=lambda *_: None)
        s = config.load_settings()
        self.assertEqual(s["default_team"], "mini")
        self.assertEqual(s["max_spawn"], 8)


class PresetTests(FakeEnv):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)
        fake_codex_home(self)

    def agent_args(self):
        return {n: r.agent_args for n, r in config.load_roles().items()}

    def test_codex_models_come_from_its_cache_listed_only_in_priority_order(self):
        self.assertEqual(presets.model_choices("codex"), ["gpt-a", "gpt-b"])
        self.assertEqual(presets.model_choices("claude"), list(presets.CLAUDE_MODELS))
        os.remove(os.path.join(os.environ["CODEX_HOME"], "models_cache.json"))
        self.assertEqual(presets.model_choices("codex"), [])

    def test_every_preset_role_has_a_researched_model_for_each_agent(self):
        for pid, p in presets.PRESETS.items():
            for r in p["roles"]:
                self.assertEqual(set(r["models"]), set(presets.AGENTS), f"{pid}/{r['name']}")
                self.assertIn(r["models"]["claude"], presets.CLAUDE_MODELS, f"{pid}/{r['name']}")

    def test_recommended_models_are_the_default_for_every_preset(self):
        fake_codex_home(self, RECOMMENDED_CODEX)
        for pid in presets.PRESETS:
            with self.subTest(pid):
                setup.run_wizard(ask=scripted([PRESET_NUMBER[pid], "", MODELS_RECOMMENDED, "", "", "", "y"]),
                                 say=lambda *_: None)
                expected = {r["name"]: ("--model", r["models"][r["agent"]]) for r in presets.PRESETS[pid]["roles"]}
                self.assertEqual(self.agent_args(), expected)

    def test_recommendation_follows_the_agent_when_it_is_changed(self):
        fake_codex_home(self, RECOMMENDED_CODEX)
        # toss-silo all codex: po is LEAD -> gpt-6-astra, frontend is CORE -> gpt-6.1-sol
        setup.run_wizard(ask=scripted([PRESET_NUMBER["toss-silo"], ALL_CODEX, MODELS_RECOMMENDED, "", "", "", "y"]),
                         say=lambda *_: None)
        args = self.agent_args()
        self.assertEqual(args["po"], ("--model", presets.LEAD["codex"]))
        self.assertEqual(args["frontend"], ("--model", presets.CORE["codex"]))

    def test_codex_recommendation_missing_from_the_catalog_falls_back_to_cli_default(self):
        said = []
        setup.run_wizard(ask=scripted(TOSS_DEFAULTS), say=said.append)     # fake catalog only has gpt-a, gpt-b
        args = self.agent_args()
        self.assertEqual(args["po"], ("--model", presets.LEAD["claude"]))
        self.assertEqual(args["frontend"], ())
        self.assertTrue(any("codex 기본 모델" in s for s in said))

    def test_per_role_pick_offers_the_recommendation_as_enter(self):
        fake_codex_home(self, RECOMMENDED_CODEX)
        # toss-silo: po Enter (fable), designer -> sonnet (4), frontend Enter (gpt-6.1-sol), server Enter, analyst Enter
        answers = [PRESET_NUMBER["toss-silo"], AGENTS_RECOMMENDED, MODELS_EACH, "", "4", "", "", "", "", "", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=lambda *_: None)
        args = self.agent_args()
        self.assertEqual(args["po"], ("--model", "fable"))
        self.assertEqual(args["designer"], ("--model", "sonnet"))
        self.assertEqual(args["frontend"], ("--model", presets.CORE["codex"]))
        self.assertEqual(args["server"], ("--model", presets.RIGOR["codex"]))

    def test_default_models_add_no_agent_args(self):
        answers = [PRESET_NUMBER["toss-silo"], AGENTS_RECOMMENDED, MODELS_DEFAULT, "", "", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=lambda *_: None)
        self.assertEqual(set(self.agent_args().values()), {()})

    def test_one_model_per_agent(self):
        # claude: 1 default, 2 fable, 3 opus, 4 sonnet, 5 custom / codex: 1 default, 2 gpt-a, 3 gpt-b, 4 custom
        answers = [PRESET_NUMBER["toss-silo"], AGENTS_RECOMMENDED, MODELS_PER_AGENT, "3", "3", "", "", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=lambda *_: None)
        args = self.agent_args()
        self.assertEqual(args["po"], ("--model", "opus"))
        self.assertEqual(args["analyst"], ("--model", "opus"))
        self.assertEqual(args["frontend"], ("--model", "gpt-b"))
        self.assertEqual(args["server"], ("--model", "gpt-b"))

    def test_models_per_role_including_typed_and_blank_custom(self):
        # baemin-tf roles: tf-lead(claude), builder(codex), qa(claude), ops(claude)
        # tf-lead -> fable (2), builder -> custom "my-model" (4; its recommendation isn't in the fake catalog),
        # qa -> Enter (= recommended opus), ops -> custom left blank (= CLI default)
        answers = [PRESET_NUMBER["baemin-tf"], AGENTS_RECOMMENDED, MODELS_EACH, "2", "4", "my-model", "", "5", "",
                   "", "", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=lambda *_: None)
        self.assertEqual(self.agent_args(), {"tf-lead": ("--model", "fable"), "builder": ("--model", "my-model"),
                                             "qa": ("--model", "opus"), "ops": ()})

    def test_every_preset_is_well_formed(self):
        for pid, p in presets.PRESETS.items():
            names = [r["name"] for r in p["roles"]]
            self.assertTrue(setup.NAME_RE.match(pid), pid)
            self.assertTrue(all(setup.NAME_RE.match(n) for n in names), pid)
            self.assertEqual(len(set(names)), len(names), pid)
            self.assertIn(p["lead"], names, pid)
            self.assertTrue(p["label"] and p["description"], pid)
            self.assertTrue(all(r["agent"] in presets.AGENTS for r in p["roles"]), pid)

    def test_every_preset_writes_a_loadable_team(self):
        for pid in presets.PRESETS:
            with self.subTest(pid):
                setup.run_wizard(ask=scripted([PRESET_NUMBER[pid], "", MODELS_DEFAULT, "", "", "", "y"]), say=lambda *_: None)
                roles = config.load_roles()
                team = config.load_team(pid, roles)
                self.assertEqual(team.lead.role, presets.PRESETS[pid]["lead"])
                self.assertEqual(team.description, presets.PRESETS[pid]["description"])
                self.assertEqual(sorted(roles), sorted(r["name"] for r in presets.PRESETS[pid]["roles"]))
                recommended = {r["name"]: r["agent"] for r in presets.PRESETS[pid]["roles"]}
                self.assertEqual({n: r.agent for n, r in roles.items()}, recommended)
                self.assertTrue(all(r.prompt for r in roles.values()))

    def test_toss_silo_defaults(self):
        team = setup.run_wizard(ask=scripted(TOSS_DEFAULTS), say=lambda *_: None)
        self.assertEqual(team, "toss-silo")
        self.assertEqual(config.load_settings()["default_team"], "toss-silo")
        roles = config.load_roles()
        members = [m.role for m in config.load_team("toss-silo", roles).members]
        self.assertEqual(members, ["po", "designer", "frontend", "server", "analyst"])
        self.assertEqual({n: r.agent for n, r in roles.items()},
                         {"po": "claude", "designer": "claude", "frontend": "codex", "server": "codex",
                          "analyst": "claude"})

    def test_agents_can_be_picked_per_role_with_recommendation_as_default(self):
        # toss-silo roles in order: po, designer, frontend, server, analyst
        # po -> codex(2), designer -> Enter (claude), frontend -> claude(1), server -> Enter (codex), analyst -> codex(2)
        answers = [PRESET_NUMBER["toss-silo"], AGENTS_EACH, "2", "", "1", "", "2", MODELS_DEFAULT, "", "", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=lambda *_: None)
        self.assertEqual({n: r.agent for n, r in config.load_roles().items()},
                         {"po": "codex", "designer": "claude", "frontend": "claude", "server": "codex",
                          "analyst": "codex"})

    def test_one_agent_for_every_role(self):
        setup.run_wizard(ask=scripted([PRESET_NUMBER["daangn-squad"], ALL_CLAUDE, MODELS_DEFAULT, "", "", "", "y"]),
                         say=lambda *_: None)
        self.assertEqual({r.agent for r in config.load_roles().values()}, {"claude"})

    def test_preset_agent_extra_role_lead_and_team_name_can_be_changed(self):
        # baemin-tf -> all codex -> add runner(shell command) -> no more -> lead = builder (#2) -> team "launch" -> save
        answers = [PRESET_NUMBER["baemin-tf"], ALL_CODEX, MODELS_DEFAULT, "y", "runner", "", "2", "pnpm dev", "n", "2", "launch", "y"]
        setup.run_wizard(ask=scripted(answers), say=lambda *_: None)
        roles = config.load_roles()
        self.assertEqual(roles["qa"].agent, "codex")
        self.assertEqual(roles["runner"].command, "pnpm dev")
        team = config.load_team("launch", roles)
        self.assertEqual(team.lead.role, "builder")
        self.assertEqual([m.role for m in team.members], ["builder", "tf-lead", "qa", "ops", "runner"])

    def test_preset_argument_skips_the_first_question(self):
        setup.run_wizard(ask=scripted(["", MODELS_DEFAULT, "", "", "", "y"]), say=lambda *_: None, preset="daangn-squad")
        self.assertEqual(config.load_team("daangn-squad", config.load_roles()).lead.role, "pm")

    def test_extra_role_cannot_reuse_a_preset_name(self):
        said = []
        answers = [PRESET_NUMBER["toss-silo"], "", MODELS_DEFAULT, "y", "po", "growth", "", "3", "n", "", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=said.append)
        self.assertIn("growth", config.load_roles())
        self.assertTrue(any("이미 추가" in s for s in said))


class FirstRunCliTests(FakeEnv):
    """`team-up` with no roles configured."""

    run_cli = test_cli.CliTests.run_cli

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)

    def with_examples(self):
        shutil.copytree(EXAMPLES, self.cfg, dirs_exist_ok=True)

    def test_without_a_terminal_it_does_not_hang_and_points_to_setup(self):
        code, _, err = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 1)
        self.assertIn("설정", err)
        self.assertEqual(len(self.fake()["panes"]), 1)

    def test_with_a_terminal_it_asks_then_builds_the_team(self):
        with mock.patch.object(cli, "_has_terminal", return_value=True), \
                mock.patch("builtins.input", side_effect=scripted(TWO_ROLES)):
            code, out, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("team 'mini'", out)
        self.assertEqual(len(self.fake()["panes"]), 2)       # lead + builder

    def test_aborting_the_wizard_exits_nonzero_and_spawns_nothing(self):
        with mock.patch.object(cli, "_has_terminal", return_value=True), \
                mock.patch("builtins.input", side_effect=scripted(TWO_ROLES[:-1] + ["n"])):
            code, _, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.fake()["panes"]), 1)

    def test_configured_roles_skip_the_wizard(self):
        self.with_examples()
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask")):
            code, _, _ = self.run_cli("team-up", "--team", "dev", "--pane", "w1:p1")
        self.assertEqual(code, 0)

    def test_setup_command_refuses_to_overwrite_without_force(self):
        self.with_examples()
        code, _, err = self.run_cli("setup")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)

    def test_list_presets_prints_every_id_without_asking(self):
        self.with_examples()
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask")):
            code, out, _ = self.run_cli("setup", "--list-presets")
        self.assertEqual(code, 0)
        for pid in presets.PRESETS:
            self.assertIn(pid, out)

    def test_unknown_preset_is_rejected_before_asking(self):
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask")):
            code, _, err = self.run_cli("setup", "--preset", "nope")
        self.assertEqual(code, 1)
        self.assertIn("toss-silo", err)
        self.assertEqual(os.listdir(self.cfg), [])

    def test_setup_with_preset_then_team_up_spawns_the_preset(self):
        with mock.patch("builtins.input", side_effect=scripted(["", MODELS_DEFAULT, "", "", "", "y", ""])):
            code, out, _ = self.run_cli("setup", "--preset", "baemin-tf")
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("team 'baemin-tf'", out)
        self.assertEqual(len(self.fake()["panes"]), 4)       # tf-lead + builder, qa, ops

    def test_chosen_models_reach_herdr_agent_start(self):
        fake_codex_home(self)
        # baemin-tf, recommended agents, one model per agent: claude -> opus (3), codex -> gpt-a (2)
        answers = ["", MODELS_PER_AGENT, "3", "2", "", "", "", "y", ""]
        with mock.patch("builtins.input", side_effect=scripted(answers)):
            code, out, _ = self.run_cli("setup", "--preset", "baemin-tf")
        self.assertEqual(code, 0, out)
        code, _, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        starts = {c[c.index("--kind") + 1] + ":" + c[2]: c[c.index("--") + 1:]
                  for c in self.fake()["calls"] if c[:2] == ["agent", "start"]}
        self.assertEqual(sorted(starts.values()), [["--model", "gpt-a"], ["--model", "opus"], ["--model", "opus"]])


if __name__ == "__main__":
    unittest.main()
