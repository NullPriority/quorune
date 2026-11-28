"""Vorinclex retains synchronous own mana and one ordinary opposing-land restraint."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.tap_state_capability_shapes import mana_tapped_land_untap_condition, mana_tapped_land_untap_effect
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from quorune.triggered_mana_model import FIXED_TRIGGERED_MANA_EVENT, LAND_MANA_TAP_EVENT
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Vorinclex, Voice of Hunger"


class ManaTappedLandNextUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing
    activate = untap_fixture.NextPlayerUntapRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "mana-tapped-land-next-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/mana-tapped-land-next-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Separate immediate mana and ordinary restraint", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_has_separate_committed_mana_and_opponent_untap_results(self):
        admitted, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        rows = semantics.programs_for_oracle(card.oracle_id)
        ordinary, = (row for row in rows if row.event == LAND_MANA_TAP_EVENT)
        immediate, = (row for row in rows if row.event == FIXED_TRIGGERED_MANA_EVENT)
        self.assertEqual(mana_tapped_land_untap_condition(), ordinary.event_condition)
        self.assertEqual([mana_tapped_land_untap_effect()], ordinary.effects)
        self.assertIsNone(ordinary.target_schema)
        self.assertFalse(immediate.effects)
        self.assertTrue(any(row.event == "continuous" for row in rows))
        for changed in (replace(ordinary, event_condition=None),
                        replace(ordinary, effects=[{"op": "prohibit_next_untap", "card": "$target.0"}])):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def ordinary(self, game, source):
        return [item for item in game.state.stack
                if item.source_object_id == source.object_id and item.context.get("event") == LAND_MANA_TAP_EVENT]

    def test_actual_own_bonus_is_immediate_and_opposing_land_misses_the_next_untap_after_source_exile(self):
        game = self.playing(10612101)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        own = self.add(game.engine, "Forest", ref="vorinclex-own-forest")
        opponent = self.add(game.engine, "Forest", seat="B", ref="vorinclex-opposing-forest")
        another = self.add(game.engine, "Island", seat="B", ref="vorinclex-unrelated-island")
        another.tapped = True
        removal = self.add(game.engine, "Swords to Plowshares", seat="C", zone="hand")
        for card in (source, removal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "G": 2})
        game.state.players["C"].mana_pool["W"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        data = game.engine._effective_card_data(game.state.cards[source.object_id])
        self.assertIn("trample", {word.casefold() for word in data["keywords"]})
        self.activate(game, "A", own)
        self.assertEqual(2, game.state.players["A"].mana_pool["G"])
        self.assertFalse(self.ordinary(game, source))
        self.activate(game, "B", opponent)
        self.assertEqual(1, game.state.players["B"].mana_pool["G"])
        pending, = self.ordinary(game, source)
        self.assertFalse(pending.targets)
        self.assertEqual({"reference": opponent.ref, "logical_object_id": opponent.logical_object_id},
                         pending.context["event_counter_recipient"])
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, game.state.cards[opponent.object_id].annotations)
        self.cast_for(game, "C", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertIs(game.state.cards[opponent.object_id].annotations[NEXT_UNTAP_PROHIBITION_ANNOTATION], True)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, game.state.cards[another.object_id].annotations)
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertEqual("upkeep", game.state.step)
        self.assertTrue(game.state.cards[opponent.object_id].tapped)
        self.assertFalse(game.state.cards[another.object_id].tapped)
        self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, game.state.cards[opponent.object_id].annotations)
        self.replay(game, load=True)

    def test_actual_blinked_land_cannot_replace_the_old_mana_tapped_incarnation(self):
        game = self.playing(10612102)
        source = self.add(game.engine, ORIGINAL)
        land = self.add(game.engine, "Forest", seat="B")
        other = self.add(game.engine, "Island", seat="B")
        flicker = self.add(game.engine, "Ghostly Flicker", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, flicker)
        self.prepare(game, actor="B", active="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.activate(game, "B", land)
        trigger, = self.ordinary(game, source)
        before = trigger.context["event_counter_recipient"]["logical_object_id"]
        self.cast_for(game, "B", flicker, [land.ref, other.ref])
        self.resolve_top(game)
        self.assertNotEqual(before, game.state.cards[land.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (land, other):
            self.assertFalse(game.state.cards[card.object_id].tapped)
            self.assertNotIn(NEXT_UNTAP_PROHIBITION_ANNOTATION, game.state.cards[card.object_id].annotations)
        self.replay(game, load=True)
