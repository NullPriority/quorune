"""Controlled creature entries compare the sealed effective stats before the private result."""

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


ORIGINAL = "Fecund Greenshell"


class EntryToughnessPrivateLandTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-toughness-private-land.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-toughness-private-land-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry toughness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_self_and_separate_controlled_entry_with_exact_snapshot(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        rows = semantics.programs_for_oracle(record.oracle_id)
        trigger, = (p for p in rows if p.event == "permanent.enter")
        self.assertEqual([private_top_land_or_hand_effect()], trigger.effects)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual({"any": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"all": [
                {"field": "controller", "op": "eq", "value": "$source.controller"},
                {"field": "types", "op": "contains_any", "value": ["creature"]},
                {"field": "power", "op": "ne", "value": None},
                {"field": "toughness", "op": "gt", "value": "$context.power"},
            ]},
        ]}, trigger.event_condition)
        self.assertTrue(any(p.handlers for p in rows))

    def prepared(self, seed, *, maha=False, armorer=False, negative=False):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        prior = self.add(game.engine, "Island", zone="library", ref="toughness-next-private")
        top = self.add(game.engine, "Sol Ring", zone="library", ref="toughness-first-private")
        next_creature = self.add(game.engine, "Runeclaw Bear", zone="hand") if armorer or negative else None
        officer = spell = foreign = None
        if maha:
            self.assert_closed(game, self.add(game.engine, "Maha, Its Feathers Night", seat="B"))
        elif armorer:
            officer = self.add(game.engine, "Veteran Armorer")
            spell = self.add(game.engine, "Doom Blade", seat="B", zone="hand")
            self.add(game.engine, "Swamp", seat="B")
            self.add(game.engine, "Wastes", seat="B")
            self.assert_closed(game, officer)
            self.assert_closed(game, spell)
        elif negative:
            foreign = self.add(game.engine, "Wall of Wood", seat="B")
            spell = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
            self.add(game.engine, "Plains", seat="B")
            self.assert_closed(game, spell)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "G": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 1, "G": 1})
        self.resolve_top(game)
        return game, source, top, prior, next_creature, officer, spell, foreign

    def choose_hand(self, game, top, *, options):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual([{"id": top.ref, "name": top.printed_name}], ctx["objects"])
        action, = ctx["legal_actions"]
        self.assertEqual(options, tuple(action["choice_schema"]["legal_values"]))
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        command = {"action_id": action["id"], "choice": "put_into_hand"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[top.object_id].zone)
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        return game

    def stats(self, game, permanent):
        return tuple(game.engine._numeric_stat(permanent.object_id, name) for name in ("power", "toughness"))

    def test_actual_own_entry_triggers_even_when_an_opposing_static_effect_lowers_toughness(self):
        game, source, top, _, _, _, _, _ = self.prepared(60306071, maha=True)
        self.assertEqual((4, 1), self.stats(game, source))
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.choose_hand(game, top, options=("put_into_hand",))
        self.replay(game, load=True)

    def test_actual_other_entry_uses_modified_stats_and_does_not_recheck_after_response(self):
        game, source, top, next_top, bear, officer, removal, _ = self.prepared(60306072, armorer=True)
        game = self.choose_hand(game, top, options=("put_into_hand",))
        self.cast_for(game, "A", bear)
        self.resolve_top(game)
        self.assertEqual((2, 3), self.stats(game, bear))
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual((2, 3), (game.state.stack[-1].context["power"], game.state.stack[-1].context["toughness"]))
        self.cast_for(game, "B", removal, [officer.ref])
        self.resolve_top(game)
        self.assertEqual((2, 2), self.stats(game, bear))
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.choose_hand(game, next_top, options=("put_onto_battlefield", "put_into_hand"))
        self.replay(game, load=True)

    def test_actual_equal_controlled_entry_and_qualifying_opposing_entry_do_not_trigger(self):
        game, source, first, next_top, bear, _, blink, wall = self.prepared(60306073, negative=True)
        game = self.choose_hand(game, first, options=("put_into_hand",))
        self.cast_for(game, "A", bear)
        self.resolve_top(game)
        self.assertEqual((2, 2), self.stats(game, bear))
        self.assertEqual(0, self.trigger_count(game, source))
        original = wall.logical_object_id
        self.cast_for(game, "B", blink, [wall.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[wall.object_id].logical_object_id)
        self.assertEqual((0, 3), self.stats(game, wall))
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(next_top.object_id, game.state.players["A"].zones["library"][-1])
        self.replay(game, load=True)
