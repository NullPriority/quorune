"""Original opponent life-gain events and occurrence-sized self-counters."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_public_event_trigger_bindings import fixed_public_event_binding_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Kavu Predator"


class OpponentLifeGainSelfCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "opponent-life-gain-self-counters.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "opponent-life-gain-self-counters-cards.json",
                                fixtures / "opponent-life-gain-to-loss-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent gain counters", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_registers_its_exact_opponent_occurrence_and_source_counters(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        trigger, = (program for program in semantics.programs_for_oracle(record.oracle_id)
                    if program.event == "life.gained")
        self.assertEqual({"field": "player", "op": "ne", "value": "$source.controller"}, trigger.event_condition)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, trigger.coverage)
        self.assertIn("trigger.event.normalized_life_gain", trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        effect, = trigger.effects
        self.assertEqual(("place_counters", "$source.zone_object", "+1/+1", "committed_event_amount"),
                         (effect["op"], effect["card"], effect["counter"], effect["amount"]["origin"]))
        original = record.oracle_text.splitlines()[-1]
        self.assertIsNotNone(fixed_public_event_binding_spec(original))
        for unsupported in (
            original.replace("an opponent", "a player"), original.replace("this creature", "target creature"),
            original.replace("+1/+1", "charge"), original + " Draw a card.",
        ):
            self.assertIsNone(fixed_public_event_binding_spec(unsupported))

    def test_actual_recipient_uses_resolved_gain_even_if_the_original_spell_is_ours(self):
        game = self.session(11990201)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        own_gain = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="predator-own-gain")
        opposite_gain = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="predator-opponent-gain")
        doubler = self.add(game.engine, "Boon Reflection", seat="B")
        for card in (source, own_gain, doubler):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 3, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"G": 2, "W": 2})
        self.resolve_top(game)
        self.assertIn("trample", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.cast_for(game, "A", own_gain, ["A"])
        self.resolve_top(game)
        self.assertEqual(47, game.state.players["A"].life)
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast_for(game, "A", opposite_gain, ["B"])
        self.resolve_top(game)
        self.assertEqual(54, game.state.players["B"].life)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(14, game.state.stack[-1].context["amount"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(14, game.state.cards[source.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_converted_gain_does_not_trigger_the_original(self):
        game = self.session(11990202)
        source = self.add(game.engine, ORIGINAL)
        converter = self.add(game.engine, "Tainted Remedy")
        gain = self.add(game.engine, "Heroes' Reunion", zone="hand")
        for card in (source, converter, gain):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", gain, ["B"])
        self.resolve_top(game)
        self.assertEqual(33, game.state.players["B"].life)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_actual_separate_lifelink_sources_queue_two_independent_public_amounts(self):
        game = self.session(11990203)
        source = self.add(game.engine, ORIGINAL, seat="B")
        small = self.add(game.engine, "Healer's Hawk")
        large = self.add(game.engine, "Child of Night")
        private = self.add(game.engine, "Humble", seat="A", zone="hand", ref="predator-private-hand")
        for card in (small, large):
            card.acquired_control_turn_count = -1
        for card in (source, small, large):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(small, "C"), (large, "C")])
        self.until(game, lambda current: getattr(current.state.pending_decision, "kind", None) == "trigger.order")
        self.assertEqual(["B"], game.state.pending_decision.actors)
        self.assertEqual((43, 37), tuple(game.state.players[seat].life for seat in "AC"))
        queued = [item for batch in game.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(queued))
        self.assertEqual({source.object_id}, {item.source_object_id for item in queued})
        self.assertEqual([1, 2], sorted(item["context"]["amount"] for item in queued))
        command = {"action_id": "order", "triggers": [item.ref for item in queued]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:B", command)
        self.assertTrue(chosen.ok, chosen.summary)
        first = game.state.stack[-1].context["amount"]
        self.resolve_top(game)
        self.assertEqual(first, game.state.cards[source.object_id].counters["+1/+1"])
        self.resolve_top(game)
        self.assertEqual(3, game.state.cards[source.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_blink_does_not_redirect_queued_counters_to_a_new_incarnation(self):
        game = self.session(11990204)
        source = self.add(game.engine, ORIGINAL)
        gain = self.add(game.engine, "Heroes' Reunion", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        for card in (source, gain, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", gain, ["B"])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        incarnation = game.state.cards[source.object_id].logical_object_id
        self.cast_for(game, "A", blink, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(incarnation, current.logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)
