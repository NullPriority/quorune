"""The original source-or-controlled-Pirate occurrence owns one optional restraint."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_tap_effect import OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION, SOURCE_CONTROL_UNTAP_FIELD
from quorune.source_untap_rule_model import ResolvedSourceUntapRestraint
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SourceOrControlledPirateOptionalUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-or-controlled-pirate-optional-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-or-controlled-pirate-optional-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original or controlled Pirate entry", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_merchant_raiders_binds_one_original_or_controlled_pirate_event(self):
        report, semantics = register_card(self.db, self.registry, "Merchant Raiders")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Merchant Raiders", fuzzy=False)
        entry, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual("permanent.enter", entry.event)
        self.assertEqual(1, entry.target_schema["up_to"])
        self.assertEqual(OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION, entry.effects[0][SOURCE_CONTROL_UNTAP_FIELD])
        for condition in (
            None,
            {"field": "subtypes", "op": "contains_any", "value": ["pirate"]},
        ):
            mutated = replace(entry, event_condition=condition)
            self.assertFalse(bind_semantic_program_runtime(
                mutated, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        with patch("quorune.compiler.source_tap_duration._OPTIONAL_CONTROLLED_SOURCE_TARGET") as missing:
            missing.fullmatch.return_value = None
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_actual_self_and_owned_pirates_trigger_once_and_ignore_the_other_arrivals(self):
        game = self.playing(61126901)
        source = self.add(game.engine, "Merchant Raiders", zone="hand")
        own = self.add(game.engine, "Spectral Sailor", zone="hand", ref="own-pirate-spell")
        other = self.add(game.engine, "Spectral Sailor", seat="B", zone="hand", ref="foreign-pirate-spell")
        ordinary = self.add(game.engine, "Wind Drake", zone="hand")
        for card in (source, own, other, ordinary):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 8})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        self.cast_for(game, "A", source, reserve={"U": 4})
        self.target(game, "A", [])
        self.assertIsNone(self.resolve(game))

        self.cast_for(game, "B", other)
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", ordinary, reserve={"U": 1})
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[other.object_id].tapped)
        self.assertFalse(any(isinstance(effect, ResolvedSourceUntapRestraint)
                             for effect in game.state.continuous_effects))

        self.cast_for(game, "A", own)
        self.target(game, "A", [other.ref])
        self.assertIsNone(self.resolve(game))
        restraint, = (effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedSourceUntapRestraint))
        self.assertEqual("A", restraint.duration_controller)
        self.assertEqual(other.object_id, restraint.locked_objects[0].object_id)
        self.until(game, lambda value: value.state.active_player == "B" and value.state.step == "upkeep")
        self.assertTrue(game.state.cards[other.object_id].tapped)
        self.replay(game, load=True)
