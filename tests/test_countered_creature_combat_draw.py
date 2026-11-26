"""Seal one public counter-at-combat fact and retain each original draw trigger."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.damage_source import DamageError, DamageSourceSnapshot
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.permanent_event_state_model import (
    COUNTERED_DAMAGE_SOURCE_CAPABILITY,
    SOURCE_PLUS_ONE_COUNTER_FIELD,
    countered_creature_combat_damage_condition,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.damage_snapshots import source_snapshot
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class CounteredCreatureCombatDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "countered-creature-combat-draw.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "countered-creature-combat-draw-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Countered creature damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_countered_creature_draw_requires_the_full_sealed_condition(self):
        record = self.db.lookup("Bred for the Hunt", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        binding, semantics = register_card(self.db, self.registry, "Bred for the Hunt")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual("damage.dealt", program.event)
        self.assertEqual(countered_creature_combat_damage_condition(), program.event_condition)
        self.assertEqual([{"op": "offer_draw", "player": "$controller", "drawer": "$controller",
                           "count": 1, "private": True}], program.effects)
        self.assertIn(COUNTERED_DAMAGE_SOURCE_CAPABILITY, program.capability_dependencies)
        weak_condition = {"all": [node for node in program.event_condition["all"]
                                  if node["field"] != SOURCE_PLUS_ONE_COUNTER_FIELD]}
        weak_dependencies = tuple(dep for dep in program.capability_dependencies
                                  if dep != COUNTERED_DAMAGE_SOURCE_CAPABILITY)
        weakened = (
            replace(program, event_condition=weak_condition),
            replace(program, capability_dependencies=weak_dependencies,
                    capability_closure=self.registry.closure(weak_dependencies, profile="commander_review").to_dict()),
        )
        for changed in weakened:
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        for printed, different in (
            ("+1/+1", "charge"),
            ("combat damage", "damage"),
            ("to a player", "to a creature"),
            ("a creature you control", "a creature"),
        ):
            with self.subTest(different=different):
                changed = replace(record, oracle_text=record.oracle_text.replace(printed, different))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def test_damage_source_counter_presence_is_optional_and_preserves_legacy_snapshots(self):
        game = self.session(74492010)
        bearer = self.add(game.engine, "Grizzly Bears")
        bearer.counters["charge"] = 1

        def captured():
            return source_snapshot(game.engine, bearer.ref, controller="A", source_card=bearer, combat=True)

        old = captured().to_dict()
        self.assertNotIn("had_plus_one_counter", old)
        self.assertEqual(old, DamageSourceSnapshot.from_dict(old).to_dict())
        self.add(game.engine, "Bred for the Hunt")
        self.assertIs(captured().had_plus_one_counter, False)
        noncombat = source_snapshot(game.engine, bearer.ref, controller="A", source_card=bearer)
        self.assertNotIn("had_plus_one_counter", noncombat.to_dict())
        bearer.counters["+1/+1"] = 1
        sealed = captured()
        self.assertIs(sealed.had_plus_one_counter, True)
        self.assertEqual(sealed, DamageSourceSnapshot.from_dict(sealed.to_dict()))
        for value in (None, 1, "true"):
            with self.subTest(value=value), self.assertRaises(DamageError):
                DamageSourceSnapshot.from_dict({**old, "had_plus_one_counter": value})
        bearer.counters.pop("+1/+1")
        self.assertIs(sealed.had_plus_one_counter, True)
        self.assertIs(captured().had_plus_one_counter, False)
        with patch("quorune.rules.damage_snapshots._source_counter_presence", return_value=True):
            with self.assertRaises(AssertionError):
                self.assertIs(captured().had_plus_one_counter, False)

    def remove_counters(self, game, seat, hexmage, card):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (action for action in actions if action["id"].startswith("activate:" + hexmage.ref + ":"))
        result = game.act(f"pilot:{seat}", {"action_id": activation["id"], "targets": [card.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[card.object_id].counters)

    def test_actual_combat_pins_each_source_counter_before_optional_draws(self):
        game = self.session(74492020)
        enchantment = self.add(game.engine, "Bred for the Hunt", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="first-countered-striker")
        second = self.add(game.engine, "Grizzly Bears", ref="second-countered-striker")
        stripped = self.add(game.engine, "Grizzly Bears", ref="counter-removed-before-damage")
        charged = self.add(game.engine, "Grizzly Bears", ref="charge-only-striker")
        before_damage = self.add(game.engine, "Vampire Hexmage", seat="B", ref="pre-damage-hexmage")
        after_damage = self.add(game.engine, "Vampire Hexmage", seat="C", ref="post-damage-hexmage")
        tucked = self.add(game.engine, "Mending Touch", zone="library", ref="combat-draw-private-card")
        for attacker in (first, second, stripped, charged):
            attacker.acquired_control_turn_count = -1
            attacker.counters["charge" if attacker is charged else "+1/+1"] = 1
        self.assert_closed(game, enchantment)
        self.prepare(game, mana={"B": 1, "G": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", enchantment)
        self.resolve_top(game)
        self.attack(game, [(first, "B"), (second, "C"), (stripped, "B"), (charged, "D")])
        self.remove_counters(game, "B", before_damage, stripped)

        self.until(game, lambda s: s.state.pending_decision.kind == "trigger.order" or self.trigger_count(s, enchantment) == 2)
        if game.state.pending_decision.kind == "trigger.order":
            offered = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
            result = game.act("pilot:A", {"action_id": "order", "triggers": offered})
            self.assertTrue(result.ok, result.summary)
        triggered = [item for item in game.state.stack if item.source_object_id == enchantment.object_id]
        self.assertEqual(2, len(triggered))
        self.assertEqual({(first.ref, "B", True), (second.ref, "C", True)}, {
            (item.context["source"], item.context["target"], item.context[SOURCE_PLUS_ONE_COUNTER_FIELD])
            for item in triggered
        })
        self.assertEqual([35, 37, 38], [game.state.players[seat].life for seat in "BCD"])
        self.remove_counters(game, "C", after_damage, second)
        self.assertEqual(2, self.trigger_count(game, enchantment))

        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        command = {"action_id": "choose", "choice": "draw"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        hand_count = len(game.state.players["A"].zones["hand"])
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIn(tucked.object_id, game.state.players["A"].zones["hand"])
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(tucked.ref, json.dumps(game.packet(viewer, full=True)))
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        result = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(hand_count + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)
