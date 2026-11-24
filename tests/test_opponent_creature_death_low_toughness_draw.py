"""Opposing creature death uses sealed final battlefield toughness for one draw."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Massacre Girl, Known Killer"


class OpponentCreatureDeathLowToughnessDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "opponent-creature-death-low-toughness-draw.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "opponent-creature-death-low-toughness-draw-cards.json",
            fixtures / "source-combat-damage-or-death-proliferate-cards.json",
            fixtures / "creature-power-at-most-controlled-islands-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent death and final toughness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_menace_wither_and_a_private_death_draw(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}
        self.assertEqual({"continuous", "characteristics.evaluate", "creature.dies"}, set(programs))
        self.assertIn("combat.block.menace", programs["continuous"].capability_dependencies)
        self.assertIn("damage.result.wither", programs["characteristics.evaluate"].capability_dependencies)
        trigger = programs["creature.dies"]
        self.assertIsNone(trigger.target_schema)
        self.assertEqual({"all": [
            {"field": "controller", "op": "ne", "value": "$source.controller"},
            {"field": "toughness", "op": "lt", "value": 1},
        ]}, trigger.event_condition)
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}], trigger.effects)

    def test_actual_wither_uses_final_toughness_and_the_queued_draw_keeps_its_controller_privately(self):
        game = self.session(70405001)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        own = self.add(game.engine, "Runeclaw Bear", ref="massacre-wither-bear")
        other = self.add(game.engine, "Runeclaw Bear", seat="B", ref="massacre-opponent-bear")
        fight = self.add(game.engine, "Prey Upon", zone="hand")
        exchange = self.add(game.engine, "Reins of Power", zone="hand")
        for card in (source, own, other, fight, exchange):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "B": 2, "G": 1, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 2, "G": 1, "U": 2})
        self.resolve_top(game)
        self.assertIn("wither", game.engine._combat_keywords(game.state.cards[own.object_id]))
        self.assertIn("menace", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.cast_for(game, "A", fight, [own.ref, other.ref])
        self.resolve_top(game)
        self.assertEqual(("graveyard", "graveyard"),
                         tuple(game.state.cards[card.object_id].zone for card in (own, other)))
        self.assertEqual(1, self.trigger_count(game, source))
        trigger = game.state.stack[-1]
        self.assertEqual(("A", "B", 0), (trigger.controller, trigger.context["controller"],
                                        trigger.context["toughness"]))

        self.cast_for(game, "A", exchange, ["C"])
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[source.object_id].controller)
        self.assertEqual("A", game.state.stack[-1].controller)
        game = self.replay(game, load=True)
        before_a = set(game.state.players["A"].zones["hand"])
        before_c = set(game.state.players["C"].zones["hand"])
        self.resolve_top(game)
        drawn, = set(game.state.players["A"].zones["hand"]) - before_a
        self.assertEqual(before_c, set(game.state.players["C"].zones["hand"]))
        private_ref = json.dumps(game.state.cards[drawn].ref)
        self.assertIn(private_ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(private_ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.replay(game, load=True)

    def test_actual_positive_toughness_death_and_same_controller_zero_toughness_do_not_draw(self):
        game = self.session(70405002)
        source = self.add(game.engine, ORIGINAL)
        own = self.add(game.engine, "Runeclaw Bear", ref="massacre-own-zero-bear")
        other = self.add(game.engine, "Runeclaw Bear", seat="B", ref="massacre-positive-bear")
        destroy = self.add(game.engine, "Doom Blade", zone="hand")
        shrink = self.add(game.engine, "Disfigure", zone="hand")
        for card in (source, destroy, shrink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 2})
        self.checkpoint(game)
        before = set(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", destroy, [other.ref], reserve={"B": 1})
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast_for(game, "A", shrink, [own.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[own.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(before - {destroy.object_id, shrink.object_id}, set(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)
