"""Risen Reef puts the private top card in hand whenever that card stays out of the battlefield."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_battlefield_model import private_top_land_or_hand_effect
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Risen Reef"


class PrivateTopLandOrHandTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-top-land-or-hand.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/private-top-land-or-hand-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private singleton and hand", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_its_elemental_event_and_mandatory_hand_alternative(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        trigger, = (row for row in semantics.programs_for_oracle(record.oracle_id)
                    if any(effect["op"] == "fixed_library_selection" for effect in row.effects))
        self.assertIsNone(trigger.target_schema)
        self.assertEqual([private_top_land_or_hand_effect()], trigger.effects)
        self.assertLessEqual({"library.select.fixed_controller", "library.select.battlefield_controller",
                              "zone.entry.tapped_state"}, set(trigger.capability_dependencies))

    def scene(self, seed, *, top_name="Sol Ring", another=False):
        game = self.session(seed)
        reef = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, reef)
        elemental = below = None
        if another:
            elemental = self.add(game.engine, "Water Elemental", zone="hand")
            below = self.add(game.engine, "Forest", zone="library", ref="reef-next-top")
            self.assert_closed(game, elemental)
        top = None
        if top_name is None:
            for object_id in tuple(game.state.players["A"].zones["library"]):
                game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
        else:
            top = self.add(game.engine, top_name, zone="library", ref="reef-private-top")
        self.prepare(game, mana={"C": 5, "G": 1, "U": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", reef, reserve={"C": 4, "U": 2})
        self.resolve_top(game)
        self.until(game, lambda current: self.trigger_count(current, reef) == 1)
        self.assertFalse(game.state.stack[-1].targets)
        return game, reef, top, elemental, below

    def choose_top(self, game, top, choice, *, options=("put_onto_battlefield", "put_into_hand")):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual([{"id": top.ref, "name": top.printed_name}], ctx["objects"])
        action, = ctx["legal_actions"]
        self.assertEqual(options, tuple(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(top.ref, json.dumps(packet))
        command = {"action_id": action["id"], "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.act("pilot:A", {**command, "choice": "keep"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_nonland_has_only_private_hand_acknowledgment_and_empty_library_auto_continues(self):
        game, _, top, _, _ = self.scene(40104091)
        count = len(game.state.players["A"].zones["hand"])
        cursor = len(game.state.events)
        game = self.choose_top(game, top, "put_into_hand", options=("put_into_hand",))
        self.assertEqual(count + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual("hand", game.state.cards[top.object_id].zone)
        self.assertNotIn("card.draw", {event.code for event in game.state.events[cursor:]})
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)
        game, _, _, _, _ = self.scene(40104092, top_name=None)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], game.state.players["A"].zones["library"])
        self.assertIn("A", game.state.active_seats())
        self.replay(game, load=True)

    def test_actual_land_choice_enters_tapped_or_moves_privately_to_hand(self):
        for index, choice in enumerate(("put_onto_battlefield", "put_into_hand")):
            with self.subTest(choice=choice):
                game, _, top, _, _ = self.scene(40104093 + index, top_name="Island")
                original = top.logical_object_id
                game = self.choose_top(game, top, choice)
                current = game.state.cards[top.object_id]
                self.assertNotEqual(original, current.logical_object_id)
                if choice == "put_into_hand":
                    self.assertEqual("hand", current.zone)
                    self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
                else:
                    self.assertEqual(("battlefield", "A", True), (current.zone, current.controller, current.tapped))
                self.replay(game, load=True)

    def test_actual_another_controlled_elemental_triggers_and_the_other_spell_does_not(self):
        game, reef, first, elemental, second = self.scene(40104095, another=True)
        game = self.choose_top(game, first, "put_into_hand", options=("put_into_hand",))
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.cast_for(game, "A", elemental)
        self.resolve_top(game)
        self.until(game, lambda current: self.trigger_count(current, reef) == 1)
        game = self.choose_top(game, second, "put_into_hand")
        self.assertEqual("hand", game.state.cards[second.object_id].zone)
        self.cast_for(game, "A", first)
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, reef))
        self.assertEqual("battlefield", game.state.cards[first.object_id].zone)
        self.replay(game, load=True)
