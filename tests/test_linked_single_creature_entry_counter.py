"""A single linked original returns with a +1/+1 counter only as a creature."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_linked_return_entry_type_counters as native
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import (
    LINKED_CREATURE_ENTRY_COUNTER_VERSION,
    LinkedExileReturnSpec,
    linked_type_entry_counter_target_schema,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class LinkedSingleCreatureEntryCounterTests(InheritedNativeFixture, unittest.TestCase):
    playing = native.LinkedReturnEntryTypeCounterTests.playing
    current = native.LinkedReturnEntryTypeCounterTests.current
    next_end = native.LinkedReturnEntryTypeCounterTests.next_end

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-single-creature-entry-counter.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-single-creature-entry-counter-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked single prospective creature return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_time_twist_requires_one_controlled_permanent_and_the_creature_only_mode(self):
        report, semantic = register_card(self.db, self.registry, "Teferi's Time Twist")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        card = self.db.lookup("Teferi's Time Twist", fuzzy=False)
        program, = semantic.programs_for_oracle(card.oracle_id)
        self.assertEqual(linked_type_entry_counter_target_schema(LINKED_CREATURE_ENTRY_COUNTER_VERSION),
                         program.target_schema)
        first, second = program.effects
        self.assertEqual(first["spec"], second["spec"])
        spec = LinkedExileReturnSpec.from_dict(first["spec"])
        self.assertEqual(LINKED_CREATURE_ENTRY_COUNTER_VERSION, spec.schema_version)
        widened = replace(program, target_schema=linked_type_entry_counter_target_schema())
        self.assertFalse(bind_semantic_program_runtime(
            widened, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.linked_exile_return_templates._SINGLE_CREATURE_ENTRY_COUNTER_RETURN", ""):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_actual_time_twist_returns_creatures_and_other_permanents_with_only_their_applicable_counters(self):
        for index, (name, expected) in enumerate((
            ("Grizzly Bears", {"+1/+1": 1}),
            ("Jace Beleren", {"loyalty": 3}),
            ("Forest", {}),
        )):
            with self.subTest(card=name):
                game = self.playing(61126610 + index)
                twist = self.add(game.engine, "Teferi's Time Twist", zone="hand")
                target = self.add(game.engine, name)
                for card in (twist, target):
                    self.assert_closed(game, card)
                if name == "Jace Beleren":
                    target.counters["loyalty"] = 6
                self.prepare(game, actor="A", active="A", mana={"U": 2})
                self.checkpoint(game)
                original = target.logical_object_id
                self.cast_for(game, "A", twist, [target.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", self.current(game, target).zone)
                game = self.replay(game, load=True)
                self.assertIsNone(self.next_end(game))
                returned = self.current(game, target)
                self.assertEqual("battlefield", returned.zone)
                self.assertNotEqual(original, returned.logical_object_id)
                self.assertEqual(expected, returned.counters)
                self.replay(game, load=True)
