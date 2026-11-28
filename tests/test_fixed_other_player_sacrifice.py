"""The printed other-player relation uses the existing simultaneous APNAP transaction."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.apnap_object_payment import OTHER_PLAYER_SACRIFICE_CAPABILITY, OTHER_PLAYER_SACRIFICE_SCOPE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class FixedOtherPlayerSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "other-player-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-other-player-sacrifice-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Other-player sacrifice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choice(self, game, seat):
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (decision.kind, decision.actors))
        self.assertFalse(game.state.stack[-1].targets)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]

    def choose(self, game, seat, ref):
        outcome = game.act("pilot:" + seat, {"action_id": "choose", "cards": [ref]})
        self.assertTrue(outcome.ok, outcome.summary)

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_actual_grave_pact_skips_controller_and_empty_seat_then_commits_together(self):
        game = self.session(76241301)
        pact = self.add(game.engine, "Grave Pact")
        destroyed = self.add(game.engine, "Grizzly Bears")
        spare = self.add(game.engine, "Llanowar Elves")
        blade = self.add(game.engine, "Doom Blade", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-first")
        second = self.add(game.engine, "Darksteel Myr", seat="C")
        self.start(game, pact, blade, mana={"B": 1, "C": 1})
        self.cast(game, "A", blade, [destroyed.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.zone(game, destroyed))
        self.assertEqual(1, self.trigger_count(game, pact))
        before = self.choice(game, "B")
        self.assertEqual([first.ref], before["options"])
        self.reject(game, "A", {"action_id": "choose", "cards": [spare.ref]})
        self.reject(game, "B", {"action_id": "choose", "cards": [second.ref]})
        self.choose(game, "B", first.ref)
        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, first), self.zone(game, second)))
        after = self.choice(game, "C")
        self.assertEqual([first.ref], after["prior_public_choices"]["B"])
        game = self.replay(game, load=True)
        self.choose(game, "C", second.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard", "battlefield"),
                         (self.zone(game, first), self.zone(game, second), self.zone(game, spare)))
        self.replay(game, load=True)

    def test_actual_rampage_retains_its_entry_token_and_matches_any_controlled_angel(self):
        game = self.session(76241302)
        source = self.add(game.engine, "Rampage of the Valkyries", zone="hand")
        angel = self.add(game.engine, "Serra Angel")
        nonangel = self.add(game.engine, "Llanowar Elves")
        body = self.add(game.engine, "Grizzly Bears", seat="B")
        first = self.add(game.engine, "Doom Blade", zone="hand", ref="first-murder")
        second = self.add(game.engine, "Doom Blade", zone="hand", ref="angel-murder")
        for index in (1, 2):
            self.add(game.engine, "Swamp", ref=f"rampage-black-land-{index}")
            self.add(game.engine, "Plains", ref=f"rampage-generic-land-{index}")
        self.start(game, source, first, second, mana={"W": 1, "B": 1, "C": 3})
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        token, = (card for card in game.state.cards.values()
                  if card.is_token and card.controller == "A" and card.zone == "battlefield")
        self.assertIn("angel", game.engine._type_parts(game.engine._effective_card_data(token)["type_line"])[1])
        self.cast(game, "A", first, [nonangel.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.zone(game, body))
        self.cast(game, "A", second, [angel.ref])
        self.resolve_top(game)
        self.assertEqual([body.ref], self.choice(game, "B")["options"])
        self.choose(game, "B", body.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield"), (self.zone(game, body), self.zone(game, token)))
        self.replay(game, load=True)

    def test_actual_shattergang_pays_each_printed_cost_before_the_correct_other_player_scope(self):
        for index, (name, color) in enumerate((("Grizzly Bears", "B"), ("Darksteel Relic", "R"), ("Omen of the Sea", "G")), start=1):
            with self.subTest(name=name):
                game = self.session(76241310 + index)
                source = self.add(game.engine, "Shattergang Brothers")
                price = self.add(game.engine, name, ref="shattergang-paid-price")
                result = self.add(game.engine, name, seat="B", ref="foreign-shattergang-result")
                self.start(game, source, mana={"C": 2, color: 1})
                self.priority(game, "A")
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action, = (row for row in actions if row["id"] == f"activate:{source.ref}:ab{index}")
                command = {"action_id": action["id"], "pay": "auto", "cost_cards": [price.ref]}
                self.reject(game, "A", {**command, "cost_cards": [result.ref]})
                result_command = game.act("pilot:A", command)
                self.assertTrue(result_command.ok, result_command.summary)
                self.assertEqual(("graveyard", "battlefield"), (self.zone(game, price), self.zone(game, result)))
                choice = self.choice(game, "B")
                self.assertEqual([result.ref], choice["options"])
                self.choose(game, "B", result.ref)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(("battlefield", "graveyard"), (self.zone(game, source), self.zone(game, result)))
                self.replay(game, load=True)

    def test_three_whole_sources_require_the_distinct_other_player_capability(self):
        for expected, (report, semantics) in enumerate((
            register_card(self.db, self.registry, "Grave Pact"),
            register_card(self.db, self.registry, "Rampage of the Valkyries"),
            register_card(self.db, self.registry, "Shattergang Brothers"),
        )):
            with self.subTest(oracle_id=report["oracle_id"]):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                programs = [program for program in semantics.programs_for_oracle(report["oracle_id"])
                            if OTHER_PLAYER_SACRIFICE_CAPABILITY in program.capability_dependencies]
                self.assertEqual(3 if expected == 2 else 1, len(programs))
                for program in programs:
                    self.assertIsNone(program.target_schema)
                    self.assertEqual(OTHER_PLAYER_SACRIFICE_SCOPE, program.effects[0]["players"])
                    deps = tuple(cap for cap in program.capability_dependencies if cap != OTHER_PLAYER_SACRIFICE_CAPABILITY)
                    absent = replace(program, capability_dependencies=deps,
                                     capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                    result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                    self.assertFalse(result["strict"])
                    self.assertIn(f"capability:undeclared_runtime_dependency:{OTHER_PLAYER_SACRIFICE_CAPABILITY}",
                                  result["blockers"])
        for text in (
            "Each other player sacrifices two creatures.", "Each other player sacrifices a basic land.",
            "Each other player sacrifices a creature of your choice.", "Each other player sacrifices another creature.",
        ):
            self.assertIsNone(fixed_affected_player_sacrifice_effect_template(text))
        card = self.db.lookup("Grave Pact", fuzzy=False)
        changed = replace(card, oracle_text=card.oracle_text.replace("of their choice.", "of their choice. Rewrite history."))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)
