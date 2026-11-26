"""Only actually committed simultaneous sacrifices supply the fixed token count."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.counted_sacrifice_tokens import COUNTED_SACRIFICE_TOKEN_CAPABILITY, counted_sacrifice_token_parts
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CommittedSacrificeTokensTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-sacrifice-tokens.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-apnap-sacrifice-token-count-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed sacrifice tokens", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def choose(self, game, seat, card):
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (decision.kind, decision.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual([card.ref], context["options"])
        command = {"action_id": "choose", "cards": [card.ref]}
        initial = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def tokens(self, game):
        return tuple(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")

    def test_actual_syphon_uses_the_final_batch_count_and_keeps_redirected_sacrifices(self):
        game = self.session(76261601)
        spell = self.add(game.engine, "Syphon Flesh", zone="hand")
        redirect = self.add(game.engine, "Rest in Peace", seat="D")
        leyline = self.add(game.engine, "Leyline of the Void")
        spared = self.add(game.engine, "Grizzly Bears", ref="own-spared-bear")
        first = self.add(game.engine, "Grizzly Bears", seat="B")
        second = self.add(game.engine, "Darksteel Myr", seat="C")
        self.add(game.engine, "Forest", seat="D")
        self.start(game, spell, redirect, leyline, mana={"C": 4, "B": 1})
        self.cast(game, "A", spell)
        self.choose(game, "B", first)
        self.assertEqual((), self.tokens(game))
        self.assertEqual(("battlefield", "battlefield"),
                         tuple(game.state.cards[card.object_id].zone for card in (first, second)))
        game = self.replay(game, load=True)
        self.choose(game, "C", second)
        for seat in "BC":
            decision = self.resolve(game)
            self.assertEqual(("replacement.order", [seat]), (decision.kind, decision.actors))
            self.assertEqual((), self.tokens(game))
            self.assertEqual(("battlefield", "battlefield"),
                             tuple(game.state.cards[card.object_id].zone for card in (first, second)))
            game = self.replay(game, load=True)
            available = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"]
            self.assertEqual(2, len(available))
            outcome = game.act("pilot:" + seat, {"action_id": "choose", "replacement": available[0]["id"]})
            self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("exile", "exile", "battlefield"),
                         tuple(game.state.cards[card.object_id].zone for card in (first, second, spared)))
        self.assertEqual([("Zombie Token", "A"), ("Zombie Token", "A")],
                         [(card.printed_name, card.controller) for card in self.tokens(game)])
        self.replay(game, load=True)

    def test_actual_no_eligible_other_creatures_produces_no_token(self):
        game = self.session(76261602)
        spell = self.add(game.engine, "Syphon Flesh", zone="hand")
        spared = self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Forest", seat="B")
        self.start(game, spell, mana={"C": 4, "B": 1})
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[spared.object_id].zone)
        self.assertEqual((), self.tokens(game))
        self.replay(game, load=True)

    def test_actual_voracious_entry_creates_one_ordinary_food_for_one_sacrifice(self):
        game = self.session(76261603)
        creature = self.add(game.engine, "Voracious Fell Beast", zone="hand")
        other = self.add(game.engine, "Grizzly Bears", seat="B")
        life = game.state.players["A"].life
        self.start(game, creature, mana={"C": 7, "B": 2})
        self.cast(game, "A", creature)
        self.choose(game, "B", other)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        food, = self.tokens(game)
        self.assertEqual(("Food Token", "A", False), (food.printed_name, food.controller, food.tapped))
        self.assertIn("flying", game.engine._combat_keywords(game.state.cards[creature.object_id]))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row.get("source") == food.ref)
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 3, game.state.players["A"].life)
        self.assertEqual((), self.tokens(game))
        self.replay(game, load=True)

    def test_complete_sources_require_the_original_participants_and_actual_count_authority(self):
        for expected_scope, (report, semantics) in (
            ("other_players", register_card(self.db, self.registry, "Syphon Flesh")),
            ("opponents", register_card(self.db, self.registry, "Voracious Fell Beast")),
        ):
            with self.subTest(oracle_id=report["oracle_id"]):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(report["oracle_id"])
                            if COUNTED_SACRIFICE_TOKEN_CAPABILITY in p.capability_dependencies)
                self.assertIsNone(program.target_schema)
                participants, _token = counted_sacrifice_token_parts(program.effects[0])
                self.assertEqual(expected_scope, participants)
                deps = tuple(cap for cap in program.capability_dependencies if cap != COUNTED_SACRIFICE_TOKEN_CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{COUNTED_SACRIFICE_TOKEN_CAPABILITY}",
                              binding["blockers"])
        card = self.db.lookup("Syphon Flesh", fuzzy=False)
        current_text = card.oracle_text.replace("sacrifices a creature.", "sacrifices a creature of their choice.")
        self.assertNotEqual(card.oracle_text, current_text)
        current_ir = compile_oracle_card(replace(card, oracle_text=current_text),
                                         capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", current_ir.status)
        for old, new in (
            ("Each other player", "Each player"), ("2/2 black Zombie", "4/4 red Goblin"),
            ("creature sacrificed", "permanent sacrificed"), ("this way.", "this way. Rewrite history."),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.resolution_effect_templates.counted_sacrifice_token_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
