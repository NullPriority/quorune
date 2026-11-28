"""Owner-bound re-entry as an enchantment before zone-entry observations."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_card_return import EVENT_ENCHANTMENT_RETURN_CAPABILITY, EVENT_ENCHANTMENT_RETURN_VERSION
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Enduring Vitality"
PROFILE = "commander_review"


class EnduringVitalityReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "enduring-vitality.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "entry-discard-and-death-reanimation-cards.json",
            fixtures / "event-player-public-count-cards.json",
            fixtures / "announced-populate-cards.json",
            fixtures / "intrinsic-permanent-death-return-cards.json",
            fixtures / "enduring-vitality-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Enduring Vitality", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_return_requires_its_sealed_creature_death_and_owning_player(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id)
                    if EVENT_ENCHANTMENT_RETURN_CAPABILITY in row.capability_dependencies)
        self.assertEqual("creature.dies.self", program.event)
        instruction, = program.effects
        self.assertEqual(EVENT_ENCHANTMENT_RETURN_VERSION, instruction["schema_version"])
        for name, substitution in (("controller", "$controller"), ("entry_form", "artifact"),
                                   ("expected_zone_change_counter", 0)):
            with self.subTest(name=name):
                altered = replace(program, effects=[{**instruction, name:substitution}])
                self.assertFalse(bind_semantic_program_runtime(
                    altered, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("if it was a creature", "if it was a token"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def test_actual_death_returns_as_enchantment_before_observations_and_replays(self):
        game = self.session(47840001)
        source = self.add(game.engine, NAME)
        watcher = self.add(game.engine, "Soul Warden")
        eidolon = self.add(game.engine, "Eidolon of Blossoms")
        other_creature = self.add(game.engine, "Grizzly Bears", ref="mana-grant-recipient")
        murder = self.add(game.engine, "Murder", zone="hand", seat="B")
        naturalize = self.add(game.engine, "Naturalize", zone="hand", seat="B", ref="hidden-second-removal")
        for index in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"swamp-{index}")
        for index in range(2):
            self.add(game.engine, "Forest", seat="B", ref=f"forest-{index}")
        for card in (source, watcher, eidolon, murder, naturalize):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A")
        self.checkpoint(game)
        initial = source.logical_object_id
        hand_size = len(game.state.players["A"].zones["hand"])
        life = game.state.players["A"].life
        self.cast(game, "B", murder, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.assertNotIn(naturalize.ref, str(game.packet("pilot:A", full=True)))
        self.settle(game)
        returned = game.state.cards[source.object_id]
        self.assertEqual(("A", "battlefield"), (returned.controller, returned.zone))
        self.assertNotEqual(initial, returned.logical_object_id)
        data = game.engine._effective_card_data(returned)
        self.assertEqual(({"enchantment"}, set()), game.engine._type_parts(data["type_line"])[:2])
        self.assertEqual(life, game.state.players["A"].life)
        self.assertEqual(hand_size + 1, len(game.state.players["A"].zones["hand"]))
        self.assertTrue(any(ability.mana_ability for ability in game.engine._activated_abilities(
            game.state.cards[other_creature.object_id])))
        self.cast(game,"B",naturalize,[returned.ref])
        self.settle(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_native_entry_witness_detects_a_return_without_the_enchantment_form(self):
        with patch("quorune.rules.entry_subtype_grants.commit_entry_enchantment_form", return_value=None):
            with self.assertRaisesRegex(AssertionError, "creature"):
                self.test_actual_death_returns_as_enchantment_before_observations_and_replays()
