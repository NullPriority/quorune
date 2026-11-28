"""The three printed originals privately inspect one top card and may put a land in tapped."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_battlefield_model import private_top_land_entry_effect
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SCOPE = "Explorer's Scope"
VEHICLES = ("Raiders' Karve", "Mobile Homestead")
ORIGINALS = (SCOPE, *VEHICLES)


class PrivateTopLandBattlefieldTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-top-land-battlefield.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/private-top-land-battlefield-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private top land", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_three_complete_originals_share_the_private_singleton_and_real_attack_scope(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                result, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(result["strict_capability_ready"], result["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                trigger, = (row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "creature.attacks")
                self.assertIsNone(trigger.target_schema)
                self.assertEqual([private_top_land_entry_effect()], trigger.effects)
                self.assertLessEqual({"library.select.fixed_controller", "library.select.battlefield_controller",
                                      "zone.entry.tapped_state"}, set(trigger.capability_dependencies))
                if name == SCOPE:
                    self.assertIn("attachment.reference.current_or_lki", trigger.capability_dependencies)
                else:
                    self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, trigger.event_condition)

    def scope_attack(self, seed, *, top_name="Forest", extra_land=False):
        game = self.session(seed)
        source = self.add(game.engine, SCOPE, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear")
        carrier.acquired_control_turn_count = -1
        top = None
        if top_name is None:
            for object_id in tuple(game.state.players["A"].zones["library"]):
                game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
        else:
            top = self.add(game.engine, top_name, zone="library", ref="private-inspected-top")
        hand = self.add(game.engine, "Island", zone="hand", ref="ordinary-land-play") if extra_land else None
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 1})
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual("declare_attackers", game.state.step)
        return game, top, hand

    def choose_looked_top(self, game, top, options, *, choice):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
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
        if options == ("keep",):
            self.assertFalse(game.act("pilot:A", {**command, "choice": "put_onto_battlefield"}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_equipped_attack_puts_one_land_tapped_and_preserves_the_turn_land_play(self):
        game, top, hand = self.scope_attack(50803091, extra_land=True)
        old = top.logical_object_id
        game = self.choose_looked_top(game, top, ("put_onto_battlefield", "keep"), choice="put_onto_battlefield")
        placed = game.state.cards[top.object_id]
        self.assertEqual(("battlefield", "A", True), (placed.zone, placed.controller, placed.tapped))
        self.assertNotEqual(old, placed.logical_object_id)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        land, = [row for row in actions if row["id"].startswith(f"play-land:{hand.ref}")]
        result = game.act("pilot:A", {"action_id": land["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[hand.object_id].zone)
        self.replay(game, load=True)

    def test_actual_nonland_is_seen_privately_and_empty_library_does_not_offer_a_move(self):
        game, top, _ = self.scope_attack(50803092, top_name="Sol Ring")
        identity = top.logical_object_id
        game = self.choose_looked_top(game, top, ("keep",), choice="keep")
        self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
        self.assertEqual(identity, game.state.cards[top.object_id].logical_object_id)
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:C", full=True)))
        self.replay(game, load=True)
        game, _, _ = self.scope_attack(50803093, top_name=None)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], game.state.players["A"].zones["library"])
        self.assertIn("A", game.state.active_seats())
        self.replay(game, load=True)

    def test_actual_paid_crew_attacks_offer_the_same_private_land_choice_on_both_vehicles(self):
        for index, name in enumerate(VEHICLES):
            with self.subTest(vehicle=name):
                game = self.session(50803094 + index)
                source = self.add(game.engine, name, zone="hand")
                pilot = self.add(game.engine, "Rumbling Baloth")
                fervor = self.add(game.engine, "Fervor")
                top = self.add(game.engine, "Forest", zone="library", ref="vehicle-private-land")
                self.assert_closed(game, source)
                self.assert_closed(game, fervor)
                self.prepare(game, mana={"C": 3})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.resolve_top(game)
                self.priority(game, "A")
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                crew, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")
                         and any(cost["k"] == "crew" for cost in row["cost_summary"].get("choose_cost", []))]
                result = game.act("pilot:A", {"action_id": crew["id"], "cost_cards": [pilot.ref]})
                self.assertTrue(result.ok, result.summary)
                self.resolve_top(game)
                self.assertTrue(game.state.cards[pilot.object_id].tapped)
                self.attack(game, [(source, "B")])
                self.until(game, lambda current: self.trigger_count(current, source) == 1)
                keep = index == 1
                game = self.choose_looked_top(
                    game, top, ("put_onto_battlefield", "keep"), choice="keep" if keep else "put_onto_battlefield",
                )
                current = game.state.cards[top.object_id]
                if keep:
                    self.assertEqual("library", current.zone)
                    self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
                    self.assertEqual(top.logical_object_id, current.logical_object_id)
                    self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
                else:
                    self.assertEqual(("battlefield", True), (current.zone, current.tapped))
                self.replay(game, load=True)
