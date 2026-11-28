"""Current Flying receivers are locked after the original untap instruction."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


RALLY = "Rally of Wings"


class ControlledFlyingResolutionStatsTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-flying-resolution-stats.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-flying-resolution-stats-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled Flying stats", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def flying(self, game, card):
        data = game.engine._effective_card_data(game.state.cards[card.object_id])
        return "flying" in {keyword.casefold() for keyword in data["keywords"]}

    def test_actual_untap_holds_triggers_and_locks_only_current_controlled_fliers(self):
        game = self.playing(61127278)
        spell = self.add(game.engine, RALLY, zone="hand")
        bird = self.add(game.engine, "Storm Crow")
        ground = self.add(game.engine, "Grizzly Bears")
        other = self.add(game.engine, "Storm Crow", seat="B", ref="other-controlled-flier")
        watcher = self.add(game.engine, "Wake Thrasher")
        late = self.add(game.engine, "Ornithopter", zone="hand")
        grant = self.add(game.engine, "Jump", zone="hand")
        frog = self.add(game.engine, "Turn to Frog", seat="B", zone="hand")
        for card in (spell, bird, ground, other, watcher, late, grant, frog):
            self.assert_closed(game, card)
        ground.tapped = True
        other.tapped = True
        self.prepare(game, actor="A", active="A", mana={"C": 2, "W": 1, "U": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", spell)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[ground.object_id].tapped)
        self.assertTrue(game.state.cards[other.object_id].tapped)
        self.assertEqual((3, 4), self.stats(game, bird))
        self.assertEqual((2, 2), self.stats(game, ground))
        self.assertEqual((1, 2), self.stats(game, other))
        self.assertEqual((1, 1), self.stats(game, watcher))
        self.assertEqual([watcher.object_id], [item.source_object_id for item in game.state.stack])
        self.assertEqual("triggered_ability", game.state.stack[-1].kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), self.stats(game, watcher))

        self.cast_for(game, "A", grant, [ground.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.flying(game, ground))
        self.assertEqual((2, 2), self.stats(game, ground))
        self.cast_for(game, "B", frog, [bird.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.flying(game, bird))
        self.assertEqual((3, 3), self.stats(game, bird))
        self.cast_for(game, "A", late)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.flying(game, late))
        self.assertEqual((0, 2), self.stats(game, late))

        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual((1, 2), self.stats(game, bird))
        self.assertEqual((2, 2), self.stats(game, ground))
        self.assertEqual((1, 1), self.stats(game, watcher))
        self.replay(game, load=True)

    def test_actual_no_fliers_still_untaps_without_an_extra_choice(self):
        game = self.playing(61127279)
        spell = self.add(game.engine, RALLY, zone="hand")
        ground = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Storm Crow", seat="B")
        self.assert_closed(game, spell)
        ground.tapped = True
        foreign.tapped = True
        self.prepare(game, actor="A", active="A", mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[ground.object_id].tapped)
        self.assertTrue(game.state.cards[foreign.object_id].tapped)
        self.assertEqual((2, 2), self.stats(game, ground))
        self.assertEqual((1, 2), self.stats(game, foreign))
        self.replay(game, load=True)

    def test_whole_source_keeps_both_operations_in_order_and_rejects_other_filters(self):
        original = self.db.lookup(RALLY, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Rally of Wings")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        spell, = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(
            ("set_public_tap_state", "modify_all_matching_permanents_until_end_of_turn"),
            tuple(effect["op"] for effect in spell.effects),
        )
        self.assertEqual(["flying"], list(spell.effects[1]["predicate"]["keywords_all"]))
        for old, new in (("with flying", "with vigilance"), ("get +2/+2", "gain vigilance"),
                         ("until end of turn", "until your next turn")):
            with self.subTest(new=new):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
