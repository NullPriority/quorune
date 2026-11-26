"""Native evidence for one player target and one shared creature snapshot."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


GUARDIAN = "Great Oak Guardian"


class TargetPlayerStatsThenUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-player-stats-then-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/target-player-stats-then-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target player stats and untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def keywords(self, game, card):
        current = game.state.cards[card.object_id]
        return {keyword.casefold() for keyword in game.engine._effective_card_data(current)["keywords"]}

    def enter(self, game, card):
        self.cast_for(game, "A", card)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertIn("reach", self.keywords(game, card))

    def test_actual_flash_targets_the_player_and_buffs_current_shrouded_creatures_before_untap_triggers(self):
        game = self.playing(61127280)
        guardian = self.add(game.engine, GUARDIAN, zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        shrouded = self.add(game.engine, "Wall of Denial", seat="B")
        watcher = self.add(game.engine, "Wake Thrasher", seat="B")
        land = self.add(game.engine, "Plains", seat="B")
        late = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
        shield = self.add(game.engine, "Aegis of the Gods", seat="C")
        for card in (guardian, own, shrouded, watcher, late, shield):
            self.assert_closed(game, card)
        own.tapped = True
        shrouded.tapped = True
        land.tapped = True
        self.prepare(game, actor="A", active="B", mana={"C": 5, "G": 1})
        self.checkpoint(game)
        self.assertEqual("B", game.state.active_player)
        self.enter(game, guardian)
        self.assertIn("shroud", self.keywords(game, shrouded))
        before = authoritative_state_hash(game.state)
        for invalid in ("C", shrouded.ref):
            denied = game.act("pilot:A", {"action_id": "choose", "targets": [invalid]})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "A", ["B"])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.resolve_top(game)
        events = game.state.events[cursor:]
        stats = next(event for event in events if event.code == "effect.mass_modify")
        untapped = next(event for event in events if event.code == "permanent.untap"
                        and event.details.get("object") == shrouded.ref)
        self.assertLess(stats.event_id, untapped.event_id)
        self.assertEqual({shrouded.ref, watcher.ref}, set(stats.details["objects"]))

        self.assertEqual((2, 10), self.stats(game, shrouded))
        self.assertFalse(game.state.cards[shrouded.object_id].tapped)
        self.assertEqual((3, 3), self.stats(game, watcher))
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertTrue(game.state.cards[own.object_id].tapped)
        self.assertEqual((2, 2), self.stats(game, own))
        self.assertEqual([watcher.object_id], [item.source_object_id for item in game.state.stack])
        self.assertEqual("B", game.state.stack[-1].controller)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((4, 4), self.stats(game, watcher))
        self.cast_for(game, "B", late)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 2), self.stats(game, late))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertEqual((0, 8), self.stats(game, shrouded))
        self.assertEqual((1, 1), self.stats(game, watcher))
        self.replay(game, load=True)

    def test_actual_player_target_without_creatures_is_legal_and_changes_no_other_set(self):
        game = self.playing(61127281)
        guardian = self.add(game.engine, GUARDIAN, zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        self.assert_closed(game, guardian)
        own.tapped = True
        self.prepare(game, actor="A", active="A", mana={"C": 5, "G": 1})
        self.checkpoint(game)
        self.enter(game, guardian)
        self.target(game, "A", ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[own.object_id].tapped)
        self.assertEqual((2, 2), self.stats(game, own))
        self.assertEqual((4, 5), self.stats(game, guardian))
        self.replay(game, load=True)

    def test_whole_source_requires_both_results_and_exact_target_player(self):
        original = self.db.lookup(GUARDIAN, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Great Oak Guardian")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        trigger, = (p for p in semantics.programs_for_oracle(original.oracle_id)
                    if p.ability_id.startswith("trigger:"))
        self.assertEqual(["player"], list(trigger.target_schema["categories"]))
        for old, new in (("Untap them.", "Tap them."), ("target player", "each opponent"),
                         ("until end of turn", "until your next turn")):
            with self.subTest(new=new):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
