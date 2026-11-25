"""Original paid graveyard return from a committed opponent life gain."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_self_return import (
    GRAVEYARD_SELF_RETURN_CAPABILITY, GRAVEYARD_SELF_RETURN_VERSION,
    graveyard_opponent_life_gain_condition, graveyard_self_return_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Punishing Fire"


class OpponentLifeGainPaidGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "opponent-life-gain-paid-graveyard-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "opponent-life-gain-paid-graveyard-return-cards.json",
                                fixtures / "opponent-life-gain-to-loss-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid graveyard return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_requires_exact_graveyard_occurrence_payment_and_return(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        damage, = (ability for ability in programs if ability.event == "resolve")
        trigger, = (ability for ability in programs if ability.event == "life.gained")
        self.assertEqual(2, damage.effects[0]["amount"])
        self.assertEqual(("graveyard", graveyard_opponent_life_gain_condition()),
                         (trigger.active_zone, trigger.event_condition))
        self.assertIn(GRAVEYARD_SELF_RETURN_CAPABILITY, trigger.capability_dependencies)
        self.assertIn("trigger.event.normalized_life_gain", trigger.capability_dependencies)
        wrapper, = trigger.effects
        result, = wrapper["effects"]
        self.assertEqual({"R": 1}, {color: amount for color, amount in wrapper["cost"].items() if amount})
        self.assertEqual(1, result[GRAVEYARD_SELF_RETURN_VERSION])
        omitted = {key: value for key, value in result.items() if key != GRAVEYARD_SELF_RETURN_VERSION}
        for invalid in (
            replace(trigger, active_zone="battlefield"),
            replace(trigger, event_condition={"field": "player", "op": "ne", "value": "$source.controller"}),
            replace(trigger, effects=({**wrapper, "cost": {**wrapper["cost"], "R": 2}},)),
            replace(trigger, effects=({**wrapper, "effects": [omitted]},)),
        ):
            with self.assertRaises(ValueError):
                graveyard_self_return_runtime_dependencies(invalid)

    def decide_return(self, game, seat, *, pay, reload=False):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [seat]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "pay": pay}
        before = authoritative_state_hash(game.state)
        foreign = "D" if seat != "D" else "C"
        self.assertFalse(game.act(f"pilot:{foreign}", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        if reload:
            game = self.replay(game, load=True)
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)
        return game

    def test_actual_damage_spell_must_reach_graveyard_before_the_gain(self):
        game = self.session(11364001)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        too_soon = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="fire-gain-too-soon")
        on_time = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="fire-gain-on-time")
        private = self.add(game.engine, "Humble", seat="B", zone="hand", ref="fire-unrelated-private")
        for card in (source, too_soon):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 2, "G": 2, "W": 2})
        self.checkpoint(game)

        self.cast_for(game, "A", too_soon, ["C"])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))

        self.cast_for(game, "A", on_time, ["C"])
        self.cast_for(game, "A", source, ["B"], reserve={"R": 1})
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(38, game.state.players["B"].life)
        self.resolve_top(game)
        self.assertEqual(54, game.state.players["C"].life)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.decide_return(game, "A", pay=True, reload=True)
        self.assertEqual(("hand", 0), (game.state.cards[source.object_id].zone,
                                      game.state.players["A"].mana_pool["R"]))
        self.replay(game, load=True)

    def test_actual_return_and_recast_leave_old_payment_payable_without_returning_the_new_card(self):
        game = self.session(11364002)
        source = self.add(game.engine, ORIGINAL, zone="graveyard")
        older = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="fire-older-trigger")
        newer = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="fire-newer-trigger")
        for card in (source, older):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 3, "G": 2, "W": 2})
        self.checkpoint(game)

        self.cast_for(game, "A", older, ["B"])
        self.resolve_top(game)
        old_trigger = game.state.stack[-1].ref
        old_id = game.state.cards[source.object_id].logical_object_id
        self.cast_for(game, "A", newer, ["C"])
        self.resolve_top(game)
        self.assertEqual(2, self.trigger_count(game, source))

        game = self.decide_return(game, "A", pay=True)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.assertEqual(old_trigger, game.state.stack[-1].ref)

        self.cast_for(game, "A", source, ["D"], reserve={"R": 1})
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("graveyard", current.zone)
        self.assertNotEqual(old_id, current.logical_object_id)
        self.assertEqual(38, game.state.players["D"].life)
        self.assertEqual(old_trigger, game.state.stack[-1].ref)

        game = self.decide_return(game, "A", pay=True, reload=True)
        self.assertEqual(("graveyard", current.logical_object_id, 0),
                         (game.state.cards[source.object_id].zone, game.state.cards[source.object_id].logical_object_id,
                          game.state.players["A"].mana_pool["R"]))
        self.replay(game, load=True)

    def test_actual_own_gain_and_opponent_conversion_do_not_offer_the_paid_return(self):
        game = self.session(11364003)
        source = self.add(game.engine, ORIGINAL, zone="graveyard")
        converter = self.add(game.engine, "Tainted Remedy")
        own = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="fire-own-gain")
        opposite = self.add(game.engine, "Heroes' Reunion", zone="hand", ref="fire-converted-gain")
        for card in (source, converter, own):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "G": 2, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", own, ["A"])
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast_for(game, "A", opposite, ["B"])
        self.resolve_top(game)
        self.assertEqual((47, 33), tuple(game.state.players[seat].life for seat in "AB"))
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_combat_lifelink_offers_graveyard_owner_the_real_price(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                game = self.session(11364010 + pay)
                source = self.add(game.engine, ORIGINAL, seat="B", zone="graveyard")
                attacker = self.add(game.engine, "Child of Night")
                mountain = self.add(game.engine, "Mountain", seat="B")
                attacker.acquired_control_turn_count = -1
                for card in (source, attacker):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(attacker, "C")])
                self.until(game, lambda current: self.trigger_count(current, source) == 1)
                self.assertEqual((42, 38), tuple(game.state.players[seat].life for seat in "AC"))
                game = self.decide_return(game, "B", pay=pay)
                self.assertEqual("hand" if pay else "graveyard", game.state.cards[source.object_id].zone)
                self.assertEqual(pay, game.state.cards[mountain.object_id].tapped)
                self.replay(game, load=True)
