"""Native end-of-combat results read the original source's current block pairs."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.end_combat_counterpart_model import (
    END_COMBAT_COUNTERPART_CAPABILITY, EndCombatCounterpartSpec, end_combat_counterpart_condition,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FROSTBEAST = "Kjeldoran Frostbeast"
WEREWOLF = "Greater Werewolf"
PROFILE = "commander_review"


class EndCombatCounterpartTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "end-combat-counterparts.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/end-combat-counterpart-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            ROOT / "tests/fixtures/all-permanent-counter-removal-and-token-exile-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Live end-of-combat creatures", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_closed_originals_share_the_exact_step_and_reject_different_scopes(self):
        for name, operation in ((FROSTBEAST, "destroy_targets"), (WEREWOLF, "place_counters_on_targets")):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(("triggered_ability", "battlefield", "step.begin"), (node.kind, node.active_zone, node.event))
                self.assertEqual(end_combat_counterpart_condition(), node.event_condition)
                effect, = node.effects
                self.assertEqual(operation, EndCombatCounterpartSpec.from_effect(effect).operation)
                self.assertIsNone(node.target_schema)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = semantics.programs_for_oracle(card.oracle_id, event="step.begin")
                for mutation in (
                    card.oracle_text.replace("blocking or blocked", "that blocked or were blocked"),
                    card.oracle_text.replace("this creature", "another creature"),
                    card.oracle_text.replace("creature blocking", "target creature blocking").replace("creatures blocking", "target creatures blocking"),
                    card.oracle_text + " Draw three cards.",
                ):
                    changed = replace(card, oracle_text=mutation)
                    result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                    self.assertFalse(any(END_COMBAT_COUNTERPART_CAPABILITY in ability.capability_dependencies
                                         for face in result.faces for ability in face.nodes))
                requirements = tuple(value for value in program.capability_dependencies if value != END_COMBAT_COUNTERPART_CAPABILITY)
                closure = self.registry.closure(requirements, profile=PROFILE).to_dict()
                omitted = replace(program, capability_dependencies=requirements, capability_closure=closure)
                raw_result = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + END_COMBAT_COUNTERPART_CAPABILITY, raw_result["blockers"])
                rebinding = replace(program, event_condition={"field": "step", "op": "eq", "value": "end_step"})
                raw_result = bind_semantic_program_runtime(rebinding, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(raw_result["strict"], raw_result)
                self.assertTrue(any(reason.startswith("runtime_end_combat_counterpart:invalid:")
                                    for reason in raw_result["blockers"]), raw_result)

    def declare(self, game, attacker, blockers):
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {card.ref: attacker.ref for card in blockers}}
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)

    def end_combat(self, game, source, *, triggered=True):
        self.until(game, lambda s: s.state.step == "end_combat")
        matching = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(int(triggered), len(matching))
        if matching:
            item, = matching
            self.assertEqual(("step.begin", "combat", "end_combat"), tuple(item.context[key] for key in ("event", "phase", "step")))
            self.assertEqual(source.logical_object_id, item.context["source_logical_object_id"])
            self.assertFalse(item.targets)

    def test_actual_frostbeast_destroys_its_live_hexproof_blocker_and_replays(self):
        game = self.session(511011)
        source = self.add(game.engine, "Kjeldoran Frostbeast")
        target = self.add(game.engine, "Slippery Bogle", seat="B")
        unrelated = self.add(game.engine, "Wall of Ice", seat="B")
        private = self.add(game.engine, "Cloudshift", seat="D", zone="hand")
        target.counters["+1/+1"] = 2
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.declare(game, source, (target,))
        self.end_combat(game, source)
        self.assertIn("Hexproof", game.engine._effective_card_data(target)["keywords"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone, "The live block pair must resolve")
        self.assertEqual("battlefield", game.state.cards[unrelated.object_id].zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_dropping_live_membership_kills_the_frostbeast_witness(self):
        with patch("quorune.end_combat_counterpart_runtime.current_end_combat_counterpart_identities", return_value=()):
            with self.assertRaisesRegex(AssertionError, "The live block pair must resolve"):
                self.test_actual_frostbeast_destroys_its_live_hexproof_blocker_and_replays()

    def test_actual_werewolf_counters_multiple_current_blockers_simultaneously(self):
        game = self.session(511012)
        source = self.add(game.engine, "Greater Werewolf")
        first = self.add(game.engine, "Wall of Ice", seat="B")
        second = self.add(game.engine, "Wall of Stone", seat="B")
        unrelated = self.add(game.engine, "Wall of Ice", seat="C", ref="unrelated-ice")
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.declare(game, source, (first, second))
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.damage")
        command = {"action_id": "assign_damage", "assignments": [
            {"source": source.ref, "target": card.ref, "amount": 1} for card in (first, second)
        ]}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.end_combat(game, source)
        self.resolve_top(game)
        for blocker, toughness in ((first, 5), (second, 6)):
            self.assertEqual({"-0/-2": 1}, game.state.cards[blocker.object_id].counters)
            self.assertEqual(toughness, game.engine._numeric_stat(blocker.object_id, "toughness"))
            self.assertEqual(0, game.engine._numeric_stat(blocker.object_id, "power"))
        self.assertEqual({}, game.state.cards[unrelated.object_id].counters)
        self.replay(game, load=True)

    def test_frostbeast_absent_at_the_step_never_triggers(self):
        game = self.session(511013)
        source = self.add(game.engine, "Kjeldoran Frostbeast")
        blocker = self.add(game.engine, "Craw Wurm", seat="B")
        source.acquired_control_turn_count = -1
        self.prepare(game)
        self.checkpoint(game)
        self.declare(game, source, (blocker,))
        self.end_combat(game, source, triggered=False)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[blocker.object_id].zone)
        self.replay(game, load=True)

    def test_blinking_either_object_after_the_trigger_removes_the_result(self):
        for blink_source in (False, True):
            with self.subTest(blink_source=blink_source):
                game = self.session(511014 + blink_source)
                source = self.add(game.engine, "Kjeldoran Frostbeast")
                blocker = self.add(game.engine, "Wall of Ice", seat="B")
                actor = "A" if blink_source else "B"
                reply = self.add(game.engine, "Cloudshift", seat=actor, zone="hand")
                self.add(game.engine, "Plains", seat=actor, ref="blink-payment")
                source.acquired_control_turn_count = -1
                self.prepare(game)
                self.checkpoint(game)
                self.declare(game, source, (blocker,))
                self.end_combat(game, source)
                chosen = source if blink_source else blocker
                original = chosen.logical_object_id
                self.cast_for(game, actor, reply, [chosen.ref])
                self.resolve_top(game)
                self.assertNotEqual(original, game.state.cards[chosen.object_id].logical_object_id)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("battlefield", game.state.cards[blocker.object_id].zone)
                self.replay(game, load=True)

    def test_actual_werewolf_replacement_order_is_private_atomic_and_replayable(self):
        game = self.session(511016)
        source = self.add(game.engine, "Greater Werewolf", seat="B")
        attacker = self.add(game.engine, "Phyrexian Walker")
        enhancer = self.add(game.engine, "Vorinclex, Monstrous Raider", seat="B")
        season = self.add(game.engine, "Doubling Season")
        private = self.add(game.engine, "Cloudshift", seat="D", zone="hand")
        attacker.counters["+0/+1"] = 8
        attacker.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        self.assert_closed(game, enhancer)
        self.assert_closed(game, season)
        self.prepare(game)
        self.checkpoint(game)
        self.declare(game, attacker, (source,))
        self.end_combat(game, source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, list(pending.actors)))
        self.assertNotIn("-0/-2", game.state.cards[attacker.object_id].counters)
        for seat in "BCD":
            self.assertNotIn("replacement.order", json.dumps(game.packet(f"pilot:{seat}", full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        pick = {"action_id": "choose", "replacement": options[0]["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", pick).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", pick)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+0/+1": 8, "-0/-2": 4}, game.state.cards[attacker.object_id].counters)
        self.assertEqual(3, game.engine._numeric_stat(attacker.object_id, "toughness"))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)
