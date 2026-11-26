from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.impulse_access_model import ImpulseAccessDuration
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


MADCAP_FIXTURE = ROOT / "tests" / "fixtures" / "madcap-jester-cards.json"


class MadcapJesterNativeWitnessTests(InheritedNativeFixture, unittest.TestCase):
    """Published action, privacy, mana, timing, and replay witness for Jester."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "madcap-jester-native.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "bound-effect-program-cards.json",
                ROOT / "tests" / "fixtures" / "defending-player-top-library-exile-cards.json",
                MADCAP_FIXTURE,
            ],
            database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Madcap Jester witness",
            [
                DeckEntry("Generic Bound Commander", 1, "commander"),
                DeckEntry("Generic Bound Plains", 30),
            ],
            ["Generic Bound Commander"],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def top(self, game, name: str, seat: str, ref: str):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=ref)
        card.known_to = []
        card.revealed_to = []
        self.assertEqual(card.object_id, game.state.players[seat].zones["library"][-1])
        return card

    def permission(self, game, card):
        return game.engine._temporary_play_permission("A", game.state.cards[card.object_id])

    def order_triggers(self, game, actor: str, expected_count: int) -> list[str]:
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        self.assertEqual([actor], game.state.pending_decision.actors)
        items = [item for batch in game.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(expected_count, len(items))
        refs = [item.ref for item in items]
        result = game.act("pilot:" + actor, {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)
        return refs

    def observe_actions(self, game, seat: str = "A"):
        self.priority(game, seat)
        packet = game.packet("pilot:" + seat, full=True)
        return packet, packet["decision"]["ctx"]["legal"]["actions"]

    def cast_options(self, game, seat: str, card):
        _, actions = self.observe_actions(game, seat)
        action = next((row for row in actions if row["id"] == "cast:" + card.ref), None)
        return action, action.get("cost_options", []) if action else []

    def tap_waste(self, game, source) -> None:
        _, actions = self.observe_actions(game)
        action, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        result = game.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)

    def resolve_item(self, game, item_ref: str) -> None:
        for _ in range(48):
            if not any(item.ref == item_ref for item in game.state.stack):
                return
            principals = game.pending_principals()
            self.assertTrue(principals, "a pending trigger must have a priority principal")
            result = game.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("The selected stack item did not resolve")

    def until_personal_turn(self, game, after_sequence: int, seat: str) -> None:
        def reached(current) -> bool:
            return current.state.turn_sequence > after_sequence and current.state.active_player == seat

        for _ in range(len(game.state.players) + 1):
            self.until(game, lambda current: reached(current) or (
                current.state.pending_decision is not None
                and current.state.pending_decision.kind == "cleanup.discard"
            ))
            if reached(game):
                return
            principal = game.pending_principals()[0]
            context = game.packet(principal, full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail(f"The next personal turn for {seat} was not reached")

    def test_independent_attack_ability_exile_grants_only_for_its_single_library_batch(self):
        game = self.session(604258002)
        jester = self.add(game.engine, "The Madcap Jester", ref="madcap-no-attack")
        ulamog = self.add(game.engine, "Ulamog, the Ceaseless Hunger", ref="independent-exiler")
        hidden = {
            "green": self.top(game, "Giant Growth", "B", "ul-go-bolt"),
            "land": self.top(game, "Forest", "B", "ul-go-land"),
            "artifact": self.top(game, "Arcane Signet", "B", "ul-go-signet"),
        }
        # Keep only those three private tops so the printed twenty-card limit
        # resolves to one opponent-library movement batch.
        for object_id in tuple(game.state.players["B"].zones["library"][:-3]):
            game.engine.move_card(object_id, "graveyard", reason="Prepare one short attacking-trigger library")
        self.assertEqual(3, len(game.state.players["B"].zones["library"]))
        self.assert_closed(game, jester)
        self.assert_closed(game, ulamog)
        game.state.config.semantic_policy = "trusted_only"
        jester = game.state.cards[jester.object_id]
        ulamog = game.state.cards[ulamog.object_id]
        jester.acquired_control_turn_count = -1
        ulamog.acquired_control_turn_count = -1
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(ulamog, "B")])
        self.assertEqual(1, self.trigger_count(game, ulamog))
        self.assertEqual(0, self.trigger_count(game, jester), "Jester itself did not attack")
        exile_trigger = game.state.stack[-1].ref
        for card in hidden.values():
            for viewer in "ABCD":
                packet = json.dumps(game.packet("pilot:" + viewer, full=True), sort_keys=True)
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)

        game = self.replay(game, load=True)
        self.resolve_item(game, exile_trigger)
        for card in hidden.values():
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", "B", False), (current.zone, current.owner, current.face_down))
            self.assertIsNone(self.permission(game, current))
        game = self.replay(game, load=True)
        self.assertEqual(1, self.trigger_count(game, game.state.cards[jester.object_id]))
        trigger_refs = [item.ref for item in game.state.stack]
        game = self.replay(game, load=True)
        for trigger_ref in trigger_refs:
            self.resolve_item(game, trigger_ref)
        for card in hidden.values():
            permission = self.permission(game, game.state.cards[card.object_id])
            self.assertIsNotNone(permission)
            self.assertEqual(("A", ImpulseAccessDuration.END_OF_NEXT_TURN.value, False),
                             (permission["player"], permission["duration"], permission["without_mana_cost"]))
        self.replay(game, load=True)

    def test_opponent_controlled_exiler_does_not_grant_jester_controller_permission(self):
        game = self.session(604258003)
        jester = self.add(game.engine, "The Madcap Jester", seat="A", ref="madcap-watcher")
        ulamog = self.add(game.engine, "Ulamog, the Ceaseless Hunger", seat="B", ref="foreign-exiler")
        hidden = {
            "green": self.top(game, "Giant Growth", "A", "foreign-growth"),
            "land": self.top(game, "Forest", "A", "foreign-land"),
            "artifact": self.top(game, "Arcane Signet", "A", "foreign-signet"),
        }
        for object_id in tuple(game.state.players["A"].zones["library"][:-3]):
            game.engine.move_card(object_id, "graveyard", reason="Prepare one short attacked library")
        self.assertEqual(3, len(game.state.players["A"].zones["library"]))
        self.assert_closed(game, jester)
        self.assert_closed(game, ulamog)
        game.state.config.semantic_policy = "trusted_only"
        jester = game.state.cards[jester.object_id]
        ulamog = game.state.cards[ulamog.object_id]
        jester.acquired_control_turn_count = -1
        ulamog.acquired_control_turn_count = -1
        self.prepare(game, active="B")
        self.checkpoint(game)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        decision = game.packet("pilot:B", full=True)["decision"]
        self.assertEqual("combat.attackers", decision["kind"])
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {ulamog.ref: "A"}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, self.trigger_count(game, ulamog))
        self.assertEqual(0, self.trigger_count(game, jester))
        exile_trigger = game.state.stack[-1].ref
        for card in hidden.values():
            for viewer in "ABCD":
                packet = json.dumps(game.packet("pilot:" + viewer, full=True), sort_keys=True)
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)

        game = self.replay(game, load=True)
        self.resolve_item(game, exile_trigger)
        self.assertEqual(0, self.trigger_count(game, game.state.cards[jester.object_id]))
        for card in hidden.values():
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", "A", False), (current.zone, current.owner, current.face_down))
            self.assertIsNone(self.permission(game, current))
        _, active_actions = self.observe_actions(game, "A")
        self.assertFalse(any(row["id"].startswith("cast:") for row in active_actions))
        self.replay(game, load=True)

    def test_actual_attack_exiles_each_opponent_top_then_grants_and_replays_play_permission(self):
        game = self.session(604258001)
        jester = self.add(game.engine, "The Madcap Jester", zone="hand", ref="madcap-jester")
        wastes = [self.add(game.engine, "Wastes", ref=f"jester-wastes-{number}") for number in (1, 2)]
        hidden_by_seat = {
            "B": self.top(game, "Forest", "B", "jester-forest"),
            "C": self.top(game, "Giant Growth", "C", "jester-growth"),
            "D": self.top(game, "Arcane Signet", "D", "jester-signet"),
        }
        self.assert_closed(game, jester)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "U": 1, "B": 1, "R": 1})
        self.cast(game, "A", jester)
        self.assertIsNone(self.resolve(game))
        jester = game.state.cards[jester.object_id]
        jester.acquired_control_turn_count = -1
        # The checkpoint includes this deliberate attack-readiness fixture.
        self.checkpoint(game)

        self.attack(game, [(jester, "B")])
        self.assertEqual(1, self.trigger_count(game, jester))
        attack_trigger_ref = game.state.stack[-1].ref
        for seat, hidden in hidden_by_seat.items():
            for viewer in "ABCD":
                packet = json.dumps(game.packet("pilot:" + viewer, full=True), sort_keys=True)
                self.assertNotIn(hidden.ref, packet, f"The {seat} library top leaked to {viewer} before exile")
                self.assertNotIn(hidden.object_id, packet, f"The {seat} physical card ID leaked to {viewer} before exile")

        game = self.replay(game, load=True)
        self.resolve_item(game, attack_trigger_ref)
        for seat, original in hidden_by_seat.items():
            card = game.state.cards[original.object_id]
            self.assertEqual(("exile", seat, False), (card.zone, card.owner, card.face_down))
            self.assertIsNone(self.permission(game, card))
            for viewer in "ABCD":
                packet = json.dumps(game.packet("pilot:" + viewer, full=True), sort_keys=True)
                self.assertIn(card.ref, packet, f"The face-up exiled card is missing for {viewer}")

        game = self.replay(game, load=True)
        self.order_triggers(game, "A", expected_count=3)
        self.assertEqual(3, self.trigger_count(game, game.state.cards[jester.object_id]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for seat, original in hidden_by_seat.items():
            current = game.state.cards[original.object_id]
            permission = self.permission(game, current)
            self.assertIsNotNone(permission, f"The {seat} exile did not receive a play grant")
            self.assertEqual("A", permission["player"])
            self.assertEqual(ImpulseAccessDuration.END_OF_NEXT_TURN.value, permission["duration"])
            self.assertFalse(permission["without_mana_cost"])

        # A gets permission to play all three cards; their owners do not.
        for seat in "BCD":
            _, other_actions = self.observe_actions(game, seat)
            self.assertFalse(any(
                row["id"] in {"cast:" + card.ref for card in hidden_by_seat.values()}
                or row["id"] in {"play-land:" + card.ref for card in hidden_by_seat.values()}
                for row in other_actions
            ))

        forest = game.state.cards[hidden_by_seat["B"].object_id]
        growth = game.state.cards[hidden_by_seat["C"].object_id]
        signet = game.state.cards[hidden_by_seat["D"].object_id]
        _, combat_actions = self.observe_actions(game, "A")
        self.assertFalse(any(row["id"] == "play-land:" + forest.ref for row in combat_actions))
        self.until(game, lambda current: current.state.active_player == "A"
                   and current.state.phase == "postcombat_main"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        game = self.replay(game, load=True)

        # Pay a green spell's {G} cost with only colorless mana; CR 609.4b
        # allows any mana type for spells cast with this permission.
        self.tap_waste(game, wastes[0])
        self.assertEqual(1, game.state.players["A"].mana_pool.get("C", 0))
        action, options = self.cast_options(game, "A", growth)
        self.assertIsNotNone(action)
        permission_options = [option for option in options if option.get("permission_source")]
        command = {
            "action_id": action["id"],
            "targets": [jester.ref],
            "pay": "manual",
            "payment": {"C": 1},
        }
        if permission_options:
            command["cost_option"] = permission_options[0]["id"]
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[growth.object_id].zone)
        self.assertEqual("C", game.state.cards[growth.object_id].owner)
        self.assertIsNone(self.permission(game, game.state.cards[growth.object_id]))

        # The Forest is still permissioned, but the normal land timing rule
        # delays its play until A's main phase with an empty stack.
        forest = game.state.cards[forest.object_id]
        _, main_actions = self.observe_actions(game, "A")
        land_action = next(row for row in main_actions if row["id"] == "play-land:" + forest.ref)
        result = game.act("pilot:A", {"action_id": land_action["id"]})
        self.assertTrue(result.ok, result.summary)
        forest = game.state.cards[forest.object_id]
        self.assertEqual(("battlefield", "B", "A"), (forest.zone, forest.owner, forest.controller))
        self.assertIsNone(self.permission(game, forest))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))

        # Leave the remaining spell in exile through A's next turn. It remains
        # playable until that turn's cleanup, then the permission disappears.
        current_turn = game.state.turn_sequence
        self.until_personal_turn(game, current_turn, "A")
        self.until(game, lambda current: current.state.active_player == "A"
                   and current.state.phase == "precombat_main"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        game = self.replay(game, load=True)
        signet = game.state.cards[signet.object_id]
        self.assertEqual("exile", signet.zone)
        permission = self.permission(game, signet)
        self.assertIsNotNone(permission)
        self.assertEqual(ImpulseAccessDuration.END_OF_NEXT_TURN.value, permission["duration"])
        for land in wastes:
            self.tap_waste(game, land)
        signet_offer, signet_options = self.cast_options(game, "A", signet)
        self.assertIsNotNone(signet_offer)
        self.assertTrue(signet_options)

        next_turn = game.state.turn_sequence
        self.until_personal_turn(game, next_turn, "B")
        self.until_personal_turn(game, next_turn, "A")
        signet = game.state.cards[signet.object_id]
        self.assertEqual("exile", signet.zone)
        self.assertIsNone(self.permission(game, signet))
        self.until(game, lambda current: current.state.active_player == "A"
                   and current.state.phase == "precombat_main"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        for land in wastes:
            self.tap_waste(game, land)
        _, expired_actions = self.observe_actions(game, "A")
        self.assertFalse(any(
            row["id"] == "cast:" + signet.ref
            for row in expired_actions
        ))
        self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
