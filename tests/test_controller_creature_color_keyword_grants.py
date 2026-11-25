"""Apply each printed color grant through the existing layer-six consumer."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SCION = "Scion of Draco"
GRANTS = {"W": "Vigilance", "U": "Hexproof", "B": "Lifelink", "R": "First Strike", "G": "Trample"}


class ControllerCreatureColorKeywordGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-color-keywords.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-creature-color-keyword-grants-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controller color grants", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_scion_keeps_domain_and_flying_with_the_five_existing_keyword_owners(self):
        card = self.db.lookup(SCION, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, SCION)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        rows = [handler for program in programs for handler in program.handlers
                if handler["handler_id"] == "continuous.ability.fixed-query-keyword-grant.v1"]
        self.assertEqual(5, len(rows))
        derived = {}
        for row in rows:
            condition = row["condition"]
            predicate = condition["predicate"]
            self.assertEqual("source_controller", condition["target_controller"])
            self.assertFalse(condition["exclude_source"])
            self.assertEqual(["battlefield"], predicate["zones"])
            self.assertEqual(["creature"], predicate["types_all"])
            color, = predicate["colors_all"]
            keyword, = row["modifier"]["add_abilities"]
            derived[color] = keyword
        self.assertEqual(GRANTS, derived)
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status)
        self.assertIn("Flying", [node.text for face in ir.faces for node in face.nodes])
        for text in (
            card.oracle_text.replace("if it's green", "if it's blue"),
            card.oracle_text.replace("if it's red", "if it's colorless"),
            card.oracle_text.replace("first strike if", "improvise if"),
            card.oracle_text.replace("creature you control", "creature your opponents control"),
            card.oracle_text + " They also have protection from everything.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status, text)

    def keywords(self, game, card):
        return game.engine._combat_keywords(game.state.cards[card.object_id])

    def test_actual_domain_payment_and_live_color_changes_update_targets_and_keywords(self):
        game = self.session(61341201)
        engine = game.engine
        scion = self.add(engine, SCION, zone="hand")
        dual = self.add(engine, "Azorius First-Wing")
        black = self.add(engine, "Walking Corpse")
        red = self.add(engine, "Goblin Piker")
        green = self.add(engine, "Grizzly Bears")
        foreign = self.add(engine, "Savannah Lions", seat="B")
        dark = self.add(engine, "Darkest Hour", zone="hand")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        self.add(engine, "Island", seat="B", ref="scion-opposing-blue")
        lands = [self.add(engine, name) for name in ("Plains", "Island", "Swamp", "Mountain", "Forest")]
        lands.extend(self.add(engine, "Swamp", ref=f"scion-extra-swamp-{index}") for index in range(2))
        for card in (scion, dual, dark, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.cast_for(game, "A", scion)
        self.assertEqual(2, sum(game.state.cards[land.object_id].tapped for land in lands))
        self.resolve_top(game)

        self.assertTrue({"vigilance", "hexproof"} <= set(self.keywords(game, dual)),
                        "Both printed color clauses must apply to the same creature")
        self.assertIn("lifelink", self.keywords(game, black))
        self.assertIn("first strike", self.keywords(game, red))
        self.assertIn("trample", self.keywords(game, green))
        self.assertNotIn("vigilance", self.keywords(game, foreign))
        self.assertIn("flying", self.keywords(game, scion))
        self.assertNotIn("lifelink", self.keywords(game, scion))

        self.cast_for(game, "A", dark)
        self.priority(game, "B")
        offered = self.action(game, "B", bounce)
        self.assertNotIn(dual.ref, offered["target_schema"]["groups"][0]["legal_refs"])
        command = {"action_id": offered["id"], "targets": [dual.ref], "pay": "auto"}
        state = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(state, authoritative_state_hash(game.state))
        self.resolve_top(game)
        self.assertNotIn("hexproof", self.keywords(game, dual))
        self.assertNotIn("vigilance", self.keywords(game, dual))
        self.assertIn("lifelink", self.keywords(game, dual))
        self.assertIn("lifelink", self.keywords(game, scion))
        self.assertIn("flying", self.keywords(game, scion))

        self.priority(game, "B")
        offered = self.action(game, "B", bounce)
        self.assertIn(dual.ref, offered["target_schema"]["groups"][0]["legal_refs"])
        self.cast_for(game, "B", bounce, [dual.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[dual.object_id].zone)
        self.replay(game, load=True)

    def test_actual_donate_moves_all_matching_grants_to_the_new_controller(self):
        game = self.session(61341202)
        engine = game.engine
        scion = self.add(engine, SCION, zone="hand")
        ours = self.add(engine, "Savannah Lions", ref="scion-original-white")
        theirs = self.add(engine, "Savannah Lions", seat="B", ref="scion-recipient-white")
        theirs_green = self.add(engine, "Grizzly Bears", seat="B")
        transfer = self.add(engine, "Donate", zone="hand")
        for card in (scion, transfer):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 14, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", scion)
        self.resolve_top(game)
        self.assertIn("vigilance", self.keywords(game, ours))
        self.assertNotIn("vigilance", self.keywords(game, theirs))
        self.cast_for(game, "A", transfer, ["B", scion.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[scion.object_id].controller)
        self.assertNotIn("vigilance", self.keywords(game, ours))
        self.assertIn("vigilance", self.keywords(game, theirs))
        self.assertIn("trample", self.keywords(game, theirs_green))
        self.assertIn("flying", self.keywords(game, scion))
        self.replay(game, load=True)

    def test_wrong_blue_keyword_is_killed_by_the_actual_scion_witness(self):
        from quorune.compiler import continuous_templates

        original = continuous_templates.fixed_query_keyword_grant_handler

        def swapped(text, **kwargs):
            if text == "blue creatures you control have hexproof.":
                text = "blue creatures you control have vigilance."
            return original(text, **kwargs)

        with patch.object(continuous_templates, "fixed_query_keyword_grant_handler", side_effect=swapped):
            with self.assertRaisesRegex(AssertionError, "Both printed color clauses"):
                self.test_actual_domain_payment_and_live_color_changes_update_targets_and_keywords()
