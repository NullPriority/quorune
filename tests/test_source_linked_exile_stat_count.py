"""A fixed self modifier counts only current cards exiled by its linked ability."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SourceLinkedExileStatCountTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-linked-exile-stat-count.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-linked-exile-stat-count-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Live associated exile count", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_retains_vigilance_and_both_linked_abilities(self):
        admitted, semantics = register_card(self.db, self.registry, "Lumbering Battlement")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        record = self.db.lookup("Lumbering Battlement", fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(3, len(programs))
        self.assertEqual(["continuous", "continuous", "permanent.enter.self"], sorted(p.event for p in programs))
        for altered in (
            record.oracle_text.replace("other nontoken creatures", "other creatures"),
            record.oracle_text.replace("+2/+2 for each", "+3/+3 for each"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=altered), capability_registry=self.registry,
                capability_profile="commander_review",
            ).status)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, characteristic)
                     for characteristic in ("power", "toughness"))

    def offer(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        return set(pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_refs"])

    def select(self, game, refs, *, allowed=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": "choose", "objects": list(refs)})
        self.assertEqual(allowed, result.ok, result.summary)
        if not allowed:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_associated_cards_raise_stats_and_leaving_exile_decreases_the_count(self):
        game = self.session(61342001)
        source = self.add(game.engine, "Lumbering Battlement", zone="hand")
        summon = self.add(game.engine, "Raise the Alarm", zone="hand")
        removed = self.add(game.engine, "Grizzly Bears", ref="linked-count-removed")
        remains = self.add(game.engine, "Grizzly Bears", ref="linked-count-remains")
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="linked-count-foreign")
        unrelated = self.add(game.engine, "Grizzly Bears", zone="exile", ref="linked-count-unrelated")
        pull = self.add(game.engine, "Pull from Eternity", zone="hand", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        for card in (source, summon, pull, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "W": 2})
        game.state.players["B"].mana_pool.update({"W": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", summon, reserve={"C": 4, "W": 1})
        self.assertIsNone(self.resolve(game))
        token = next(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.cast_for(game, "A", source)
        self.assertEqual({removed.ref, remains.ref}, self.offer(game))
        self.assertEqual((4, 5), self.stats(game, source))
        self.assertIn("vigilance", game.engine._combat_keywords(game.state.cards[source.object_id]))
        for card in (source, opponent, token, unrelated):
            self.select(game, [card.ref], allowed=False)
        game = self.replay(game, load=True)
        self.select(game, [removed.ref, remains.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((8, 9), self.stats(game, source))
        self.cast_for(game, "B", pull, [removed.ref], reserve={"U": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[removed.object_id].zone)
        self.assertEqual((6, 7), self.stats(game, source))
        self.cast_for(game, "B", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[remains.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[removed.object_id].zone)
        self.assertEqual("exile", game.state.cards[unrelated.object_id].zone)
        self.replay(game, load=True)

    def test_actual_copied_entry_adds_a_second_batch_to_the_same_original_link(self):
        game = self.session(61342002)
        source = self.add(game.engine, "Lumbering Battlement", zone="hand")
        resonator = self.add(game.engine, "Strionic Resonator")
        first = self.add(game.engine, "Grizzly Bears", ref="linked-count-copy-first")
        second = self.add(game.engine, "Grizzly Bears", ref="linked-count-copy-second")
        for card in (source, resonator):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 2})
        self.resolve_top(game)
        trigger = game.state.stack[-1]
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (row for row in actions if row["id"].startswith(f"activate:{resonator.ref}:"))
        result = game.act("pilot:A", {"action_id": activation["id"], "targets": [trigger.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(2, len(game.state.stack))
        self.assertEqual({first.ref, second.ref}, self.offer(game))
        self.select(game, [first.ref])
        self.assertEqual((6, 7), self.stats(game, source))
        self.assertEqual({second.ref}, self.offer(game))
        self.select(game, [second.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((8, 9), self.stats(game, source))
        self.assertEqual(["exile", "exile"], [game.state.cards[card.object_id].zone for card in (first, second)])
        self.replay(game, load=True)
