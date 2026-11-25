"""Native evidence for a life total that defines both source statistics in every zone."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import (
    CharacteristicFragmentError, CharacteristicQuantitySpec, PowerToughnessCalculation,
    QueryCharacteristicModifierSpec, QueryPowerToughnessDefinitionSpec,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Serra Avatar", "Soul of Eternity")


class LifeTotalDefiningPowerToughnessTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "life-total-defining-power-toughness.sqlite3"
        fixture = ROOT / "tests/fixtures/life-total-defining-power-toughness-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("All-zone life statistics", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, semantics.programs_for_oracle(card.oracle_id)

    def stats(self, game, source):
        values = game.engine._effective_card_data(game.state.cards[source.object_id])
        return tuple(int(values[key]) for key in ("power", "toughness"))

    def test_complete_originals_use_one_all_zone_definition_and_keep_their_other_abilities(self):
        for name, other_event in zip(ORIGINALS, ("object.enter_graveyard", "activate"), strict=True):
            with self.subTest(name=name):
                card, programs = self.program(name)
                self.assertIn(other_event, {program.event for program in programs})
                definition, = [item for item in programs if item.active_zone == "all"]
                self.assertEqual("continuous", definition.event)
                self.assertIsNone(definition.target_schema)
                handler, = definition.handlers
                fragment = handler["fragment"]["value"]
                self.assertEqual("query_power_toughness_definition", handler["fragment"]["kind"])
                self.assertTrue(fragment["define_power"] and fragment["define_toughness"])
                for altered in (
                    card.oracle_text.replace("your life total", "an opponent's life total"),
                    card.oracle_text.replace("your life total", "the life total you had when this creature entered"),
                    card.oracle_text.replace("your life total.", "your life total. Draw a card."),
                ):
                    ir = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry)
                    self.assertNotEqual("exact", ir.status, altered)

    def test_signed_definition_codec_rejects_offsets_inexact_roles_and_modifier_reuse(self):
        _, programs = self.program("Serra Avatar")
        defining, = [program for program in programs if program.active_zone == "all"]
        fragment = QueryPowerToughnessDefinitionSpec.from_dict(defining.handlers[0]["fragment"]["value"])
        encoded = fragment.quantity.to_dict()
        self.assertEqual(fragment.quantity, CharacteristicQuantitySpec.from_dict(encoded))
        for altered in ({**encoded, "schema_version": 1}, {**encoded, "exclude_source": True},
                        {**encoded, "offset": 1}):
            with self.assertRaises(CharacteristicFragmentError):
                CharacteristicQuantitySpec.from_dict(altered)
        with self.assertRaises(CharacteristicFragmentError):
            replace(fragment, define_toughness=False)
        with self.assertRaises(CharacteristicFragmentError):
            QueryCharacteristicModifierSpec(fragment.quantity,
                PowerToughnessCalculation.PER_MATCHING_OBJECT, 1, 1)

    def test_off_battlefield_life_definition_retains_a_signed_state_value(self):
        game = self.session(6043610)
        source = self.add(game.engine, "Serra Avatar", zone="hand")
        self.assert_closed(game, source)
        game.state.players["A"].life = -3
        self.assertEqual((-3, -3), self.stats(game, source))

    def test_actual_borrowed_avatar_uses_controller_for_a_paid_sacrifice_and_owner_after_arrival(self):
        game = self.session(6043620)
        avatar = self.add(game.engine, "Serra Avatar")
        game.engine.change_control(avatar.object_id, "B", reason="Prepare a borrowed Avatar")
        fling = self.add(game.engine, "Fling", seat="B", zone="hand")
        for card in (avatar, fling):
            self.assert_closed(game, card)
        game.state.players["A"].life = 18
        game.state.players["B"].life = 11
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "R": 1})
        self.checkpoint(game)
        self.assertEqual((11, 11), self.stats(game, avatar))
        self.priority(game, "B")
        action = self.action(game, "B", fling)
        cast = game.act("pilot:B", {"action_id": action["id"], "targets": ["C"],
                                    "sacrifice_cards": [avatar.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("graveyard", game.state.cards[avatar.object_id].zone)
        self.assertEqual((18, 18), self.stats(game, avatar))
        self.assertEqual(1, self.trigger_count(game, avatar))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("library", game.state.cards[avatar.object_id].zone)
        self.assertEqual((18, 18), self.stats(game, avatar))
        self.resolve_top(game)
        self.assertEqual(29, game.state.players["C"].life)
        self.assertNotIn(avatar.ref, str(game.packet("pilot:B", full=True)["state"]))
        self.replay(game, load=True)

    def test_actual_encore_copies_reevaluate_life_and_retain_their_separate_attack_plans(self):
        game = self.session(6043630)
        source = self.add(game.engine, "Soul of Eternity", zone="graveyard")
        gain = self.add(game.engine, "Revitalize", zone="hand")
        for card in (source, gain):
            self.assert_closed(game, card)
        game.state.players["A"].life = 14
        self.prepare(game, mana={"C": 8, "W": 3})
        self.checkpoint(game)
        self.assertEqual((14, 14), self.stats(game, source))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:A", {"action_id": action, "pay": "manual", "payment": {"C": 7, "W": 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        copies = [card for card in game.state.cards.values()
                  if card.oracle_id == source.oracle_id and card.is_token and card.zone == "battlefield"]
        self.assertEqual(3, len(copies))
        self.assertEqual({"B", "C", "D"}, {card.annotations["encore_attack_if_able"]["opponent"] for card in copies})
        self.assertTrue(all(self.stats(game, card) == (14, 14) for card in copies))
        self.cast_for(game, "A", gain)
        self.resolve_top(game)
        self.assertEqual(17, game.state.players["A"].life)
        self.assertTrue(all(self.stats(game, card) == (17, 17) for card in copies))
        self.assertEqual((17, 17), self.stats(game, source))
        self.replay(game, load=True)
