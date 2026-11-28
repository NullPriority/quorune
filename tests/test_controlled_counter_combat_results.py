"""Native proof for an optional counter move and the countered combat result."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_counter_movement_model import OPTIONAL_COMBAT_COUNTER_MOVEMENT_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.permanent_event_state_model import (
    COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY, any_counter_combat_damage_condition,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.trigger_limits import OPTIONAL_RESULT_USED_HISTORY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Tidus, Yuna's Guardian"
PROFILE = "commander_review"


class ControlledCounterCombatResultsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-counter-combat.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-counter-combat-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Counter combat", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, source, *companions):
        game.state.config.semantic_policy = "trusted_only"
        for card in (source, *companions):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1, "W": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def target_move(self, game, donor, recipient, *, opponent=None):
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        self.assertEqual(["A"], game.state.pending_decision.actors)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(["donor", "recipient"], [group["id"] for group in ctx["target_schema"]["groups"]])
        if opponent is not None:
            self.reject(game, "A", {"action_id": "choose", "targets": [donor.ref, opponent.ref]})
            self.reject(game, "A", {"action_id": "choose", "targets": [opponent.ref, recipient.ref]})
            self.reject(game, "A", {"action_id": "choose", "targets": [donor.ref, donor.ref]})
            self.reject(game, "B", {"action_id": "choose", "targets": [donor.ref, recipient.ref]})
        self.act(game, "A", {"action_id": "choose", "targets": [donor.ref, recipient.ref]})

    def choice_schema(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        offer, = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]
        return offer["choice_schema"]

    def counters(self, game, card):
        return game.state.cards[card.object_id].counters

    def body(self, game, name, *, ref, seat="A", counters=None):
        card = self.add(game.engine, name, seat=seat, ref=ref)
        card.acquired_control_turn_count = -1
        card.counters.update(counters or {})
        return card

    def used(self, game):
        return game.engine._current_turn_history(OPTIONAL_RESULT_USED_HISTORY)

    def test_actual_beginning_of_combat_moves_chosen_counter_between_original_creatures(self):
        game = self.session(603020081)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        donor = self.body(game, "Grizzly Bears", ref="tidus-move-donor", counters={"charge": 2, "shield": 1})
        recipient = self.body(game, "Grizzly Bears", ref="tidus-move-recipient")
        other = self.body(game, "Grizzly Bears", seat="B", ref="tidus-move-other", counters={"quest": 1})
        self.ready(game, source)
        self.target_move(game, donor, recipient, opponent=other)
        schema = self.choice_schema(game)
        self.assertEqual("counter_kind", schema["field"])
        self.assertEqual(["charge", "shield"], schema["legal_values"])
        self.assertTrue(schema["optional"])
        self.reject(game, "A", {"action_id": "choose", "counter_kind": "quest"})
        self.reject(game, "B", {"action_id": "choose", "counter_kind": "charge"})
        game = self.replay(game, load=True)
        self.act(game, "A", {"action_id": "choose", "counter_kind": "charge"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"charge": 1, "shield": 1}, self.counters(game, donor))
        self.assertEqual({"charge": 1}, self.counters(game, recipient))
        self.assertEqual({"quest": 1}, self.counters(game, other))
        self.replay(game, load=True)

    def test_actual_counter_move_can_decline_or_lose_the_original_second_target(self):
        for bounce_away in (False, True):
            with self.subTest(bounce_away=bounce_away):
                game = self.session(603020082 + bounce_away)
                source = self.add(game.engine, ORIGINAL, zone="hand")
                donor = self.body(game, "Grizzly Bears", ref="tidus-optional-donor", counters={"charge": 2})
                recipient = self.body(game, "Grizzly Bears", ref="tidus-optional-recipient")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.add(game.engine, "Island", seat="B")
                self.ready(game, source, bounce)
                self.target_move(game, donor, recipient)
                if bounce_away:
                    self.cast(game, "B", bounce, [recipient.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[recipient.object_id].zone)
                else:
                    self.assertEqual(["charge"], self.choice_schema(game)["legal_values"])
                    self.act(game, "A", {"action_id": "choose"})
                self.assertIsNone(self.resolve(game))
                self.assertEqual({"charge": 2}, self.counters(game, donor))
                self.assertEqual({}, self.counters(game, recipient))
                self.replay(game, load=True)

    def test_actual_cheer_groups_by_damaged_player_and_can_decline_then_draw_and_proliferate(self):
        game = self.session(603020084)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        attackers = [self.body(game, "Grizzly Bears", ref=f"tidus-attacker-{index}", counters=counters)
                     for index, counters in enumerate(({"charge": 1}, {"shield": 1}, {"+1/+1": 1}, {}))]
        proliferated = self.add(game.engine, "Forest", seat="D", ref="tidus-public-proliferation")
        proliferated.counters.update({"charge": 2, "quest": 1})
        private = self.add(game.engine, "Forest", zone="library", ref="tidus-private-draw")
        self.ready(game, source)
        self.target_move(game, attackers[0], attackers[1])
        self.choice_schema(game)
        self.act(game, "A", {"action_id": "choose"})
        hand = len(game.state.players["A"].zones["hand"])
        self.attack(game, list(zip(attackers, "BBCD", strict=True)))
        self.until(game, lambda current: current.state.pending_decision.kind == "trigger.order"
                   or self.trigger_count(current, source) > 0)
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        triggers = [item for group in game.state.pending_trigger_batches for item in group.items
                    if item.source_object_id == source.object_id]
        self.assertCountEqual(["B", "C"], [item["context"]["player"] for item in triggers])
        self.act(game, "A", {"action_id": "order", "triggers": [item.ref for item in triggers]})
        self.assertEqual(2, self.trigger_count(game, source))
        self.assertEqual(["apply", "decline"], self.choice_schema(game)["legal_values"])
        self.act(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertEqual((), self.used(game))
        self.assertEqual(hand, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(["apply", "decline"], self.choice_schema(game)["legal_values"])
        game = self.replay(game, load=True)
        self.act(game, "A", {"action_id": "choose", "choice": "apply"})
        schema = self.choice_schema(game)
        self.assertIn(proliferated.ref, schema["legal_refs"])
        self.assertEqual(hand + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual("hand", game.state.cards[private.object_id].zone)
        for seat in "BCD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        command = {"action_id": "choose", "objects": [proliferated.ref]}
        self.reject(game, "D", command)
        game = self.replay(game, load=True)
        self.act(game, "A", command)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"charge": 3, "quest": 2}, self.counters(game, proliferated))
        used, = self.used(game)
        self.assertEqual(("A", game.state.cards[source.object_id].logical_object_id), (used.actor, used.object_incarnation))
        self.replay(game, load=True)

    def test_actual_accepted_cheer_suppresses_later_damage_that_turn(self):
        game = self.session(603020085)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        striker = self.body(game, "Fencing Ace", ref="tidus-double-striker", counters={"quest": 1})
        self.ready(game, source, striker)
        self.target_move(game, striker, source)
        self.choice_schema(game)
        self.act(game, "A", {"action_id": "choose"})
        self.attack(game, [(striker, "B")])
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual(39, game.state.players["B"].life)
        self.choice_schema(game)
        self.act(game, "A", {"action_id": "choose", "choice": "apply"})
        self.choice_schema(game)
        self.act(game, "A", {"action_id": "choose", "objects": []})
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.players["B"].life == 38)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(1, len(self.used(game)))
        self.replay(game, load=True)

    def test_complete_original_requires_ordered_controlled_targets_and_limited_combat_result(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        move_cap = OPTIONAL_COMBAT_COUNTER_MOVEMENT_CAPABILITY
        cheer_cap = COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            move, cheer = ir.faces[0].nodes
            self.assertTrue(move.target_schema["globally_distinct"])
            self.assertEqual(["you", "you"], [group["controller_relation"] for group in move.target_schema["groups"]])
            self.assertEqual(any_counter_combat_damage_condition(), cheer.event_condition)
            self.assertEqual(4, cheer.trigger_limit["schema_version"])
            self.assertTrue(cheer.text.startswith("Cheer "))
        report, semantics = register_card(self.db, self.registry, "Tidus, Yuna's Guardian")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(programs))
        move, = (row for row in programs if move_cap in row.capability_dependencies)
        without_move = tuple(key for key in move.capability_dependencies if key != move_cap)
        changed = replace(move, capability_dependencies=without_move,
                          capability_closure=self.registry.closure(without_move, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        cheer, = (row for row in programs if cheer_cap in row.capability_dependencies)
        without_cheer = tuple(key for key in cheer.capability_dependencies if key != cheer_cap)
        with self.assertRaisesRegex(ValueError, "usage capability dependency"):
            replace(cheer, capability_dependencies=without_cheer,
                    capability_closure=self.registry.closure(without_cheer, profile=PROFILE).to_dict())
        wrapper = cheer.effects[0]
        with self.assertRaisesRegex(ValueError, "supported controller choice"):
            replace(cheer, effects=[{**wrapper, "effects": list(reversed(wrapper["effects"]))}])
        for changed in (
            replace(cheer, trigger_limit=None),
            replace(cheer, coverage=[key for key in cheer.coverage if key != "one_or_more_event_batch"]),
        ):
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
