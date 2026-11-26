"""Tunnel Ignus uses committed individual land arrivals in the same turn."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.land_entry_condition_model import (
    OTHER_LAND_ARRIVAL_COVERAGE, OTHER_LAND_ARRIVAL_EVENT, opponent_other_land_arrival_condition,
    other_land_arrival_damage_effect,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Tunnel Ignus"


class OpponentLandEntryHistoryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-land-entry-history.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-land-entry-history-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Count committed arrivals", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_owns_the_opponent_arrival_and_intervening_recheck(self):
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        trigger, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(OTHER_LAND_ARRIVAL_EVENT, trigger.event)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(opponent_other_land_arrival_condition(), trigger.event_condition)
        self.assertEqual([other_land_arrival_damage_effect()], trigger.effects)
        self.assertTrue({OTHER_LAND_ARRIVAL_COVERAGE, "intervening_condition"} <= set(trigger.coverage))
        self.assertFalse(bind_semantic_program_runtime(
            replace(trigger, coverage=[name for name in trigger.coverage if name != "intervening_condition"]),
            capability_registry=self.registry, profile="commander_review",
        )["strict"])
        for mutated in (
            card.oracle_text.replace("this turn", "last turn"),
            card.oracle_text.replace("had another land enter the battlefield", "played another land"),
            card.oracle_text.replace("3 damage", "2 damage"),
        ):
            ir = compile_oracle_card(
                replace(card, oracle_text=mutated), capability_registry=self.registry, capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)

    def play_land(self, game, seat, card):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row.get("action") == "play_land" and row.get("card") == card.ref)
        result = game.act("pilot:" + seat, {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[card.object_id].zone)

    def test_actual_simultaneous_opponent_lands_each_trigger_and_survive_source_departure(self):
        game = self.session(60304061)
        source = self.add(game.engine, ORIGINAL)
        flicker = self.add(game.engine, "Ghostly Flicker", seat="B", zone="hand")
        shock = self.add(game.engine, "Shock", seat="C", zone="hand")
        lands = [self.add(game.engine, name, seat="B") for name in ("Forest", "Island")]
        self.add(game.engine, "Mountain", seat="C")
        for card in (source, flicker, shock):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", flicker, [card.ref for card in lands])
        self.resolve_top(game)
        self.until(game, lambda current: self.trigger_count(current, source) == 2)
        land_triggers = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual({"B"}, {item.context["controller"] for item in land_triggers})
        self.assertEqual({game.state.cards[card.object_id].logical_object_id for card in lands},
                         {item.context["card_object_identity"] for item in land_triggers})
        life = game.state.players["B"].life
        self.cast_for(game, "C", shock, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(life, game.state.players["B"].life)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 6, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_first_land_is_ignored_and_its_paid_departure_preserves_the_next_trigger(self):
        game = self.session(60304062)
        source = self.add(game.engine, ORIGINAL)
        first = self.add(game.engine, "Forest", seat="B", zone="hand")
        second = self.add(game.engine, "Mountain", seat="B", zone="library", ref="private-rotation-choice")
        spell = self.add(game.engine, "Crop Rotation", seat="B", zone="hand")
        for card in (source, spell):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"G": 1})
        self.checkpoint(game)
        self.play_land(game, "B", first)
        self.assertEqual(0, self.trigger_count(game, source))
        cast = self.action(game, "B", spell)
        paid = game.act("pilot:B", {"action_id": cast["id"], "pay": "auto", "sacrifice_cards": [first.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        search = self.resolve(game)
        self.assertEqual(("semantic.search", ["B"]), (search.kind, search.actors))
        self.assertNotIn(second.ref, json.dumps(game.packet("pilot:A", full=True)))
        command = {"action_id": "choose", "search_cards": [second.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual("battlefield", game.state.cards[second.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        life = game.state.players["B"].life
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 3, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_own_land_arrivals_cannot_supply_an_opponents_first_land(self):
        game = self.session(60304063)
        source = self.add(game.engine, ORIGINAL)
        flicker = self.add(game.engine, "Ghostly Flicker", zone="hand")
        lands = [self.add(game.engine, name) for name in ("Forest", "Island")]
        first_for_b = self.add(game.engine, "Mountain", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, flicker)
        self.prepare(game, active="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", flicker, [card.ref for card in lands])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self.trigger_count(game, source))
        self.play_land(game, "B", first_for_b)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)
