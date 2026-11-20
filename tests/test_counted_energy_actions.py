"""Native resolution-time counts for player energy placement."""

import json
import tempfile
import unittest
from pathlib import Path

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CountedEnergyActionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / 'counted-energy.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/counted-energy-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Counted energy originals', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    def test_original_robobrain_counts_after_response_and_handles_zero(self):
        for expected_energy in (1, 0):
            with self.subTest(expected_energy=expected_energy):
                session = self.session(107_014_001 + expected_energy)
                robot = self.add(session.engine, 'Robobrain War Mind', zone='hand')
                response = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
                ring = self.add(session.engine, 'Sol Ring')
                opponent = self.add(session.engine, 'Ornithopter', seat='C', ref='opposing-thopter')
                private = self.add(session.engine, 'Generic Bound Body', seat='C',
                                   zone='hand', ref='private-energy-control')
                removed = (self.add(session.engine, 'Ornithopter', ref='friendly-thopter')
                           if expected_energy else robot)
                for card in (robot, response, ring, opponent, removed):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'C': 3, 'U': 1})
                session.state.players['B'].mana_pool['U'] = 1
                self.checkpoint(session)

                self.cast(session, 'A', robot)
                self.resolve_top(session)
                self.assertEqual('battlefield', session.state.cards[robot.object_id].zone)
                self.assertEqual(1, len(session.state.stack))
                self.assertEqual(0, session.state.players['A'].energy)

                self.priority(session, 'B')
                offer = self.action(session, 'B', session.state.cards[response.object_id])
                before = authoritative_state_hash(session.state)
                rejected = session.act('pilot:B', {
                    'action_id': offer['id'], 'targets': [ring.ref], 'pay': 'auto',
                })
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast_for(session, 'B', response, [removed.ref])
                self.resolve_top(session)
                self.assertEqual('hand', session.state.cards[removed.object_id].zone)
                self.assertEqual(1, len(session.state.stack))

                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(expected_energy, session.state.players['A'].energy)
                self.assertEqual([0, 0, 0], [session.state.players[seat].energy for seat in 'BCD'])
                energy_placements = [event.details['placed'] for event in session.state.events
                                     if event.code == 'counter.add' and event.details['counter'] == 'energy']
                self.assertEqual([expected_energy] if expected_energy else [], energy_placements)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
                self.replay(session)

    def test_original_scurry_counts_creatures_after_creating_gremlins(self):
        session = self.session(107_014_003)
        scurry = self.add(session.engine, 'Scurry of Gremlins', zone='hand')
        self.add(session.engine, 'Generic Bound Body', ref='friendly-creature')
        self.add(session.engine, 'Ornithopter', seat='C', ref='opposing-creature')
        self.assert_closed(session, scurry)
        self.prepare(session, mana={'C': 2, 'R': 1, 'W': 1})
        self.checkpoint(session)

        self.cast(session, 'A', scurry)
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values()
                  if card.object_kind == 'token' and card.zone == 'battlefield']
        self.assertEqual(['A', 'A'], [card.controller for card in tokens])
        self.assertEqual(3, session.state.players['A'].energy)
        self.replay(session)


class CombatDamageEnergyShapeTests(unittest.TestCase):
    def program(self):
        from types import SimpleNamespace

        from quorune.rules.event_player_result_shapes import combat_damage_energy_effect

        return SimpleNamespace(
            event="damage.dealt.self", active_zone="battlefield", target_schema=None, cost_schema=None,
            effects=(combat_damage_energy_effect(),),
            event_condition={"all": [
                {"field": "target_kind", "op": "eq", "value": "player"},
                {"field": "combat", "op": "truthy", "value": True},
                {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
            ]},
        )

    def test_exact_effect_reuses_fixed_counter_and_scalar_owners(self):
        from quorune.rules.event_player_result_shapes import (
            combat_damage_energy_fixed_projection,
            combat_damage_energy_runtime_dependencies,
        )
        from quorune.rules.node_capability_shapes import fixed_player_counter_placement_node_capabilities

        program = self.program()
        result, = program.effects
        fixed = combat_damage_energy_fixed_projection(result)
        self.assertEqual({**result, "amount": 2}, fixed)
        self.assertEqual(("counter.producer.fixed_player_effect",),
            fixed_player_counter_placement_node_capabilities(
                effects=(fixed,), target_schema=None, mechanic_ids={"cr-122-counters"}))
        self.assertEqual({"quantity_expression.scalar_effect_amount", "counter.producer.fixed_player_effect",
                          "trigger.event.normalized_damage", "trigger.placement.apnap"},
                         set(combat_damage_energy_runtime_dependencies(program)))

    def test_raw_descriptor_rejects_unbound_or_foreign_counter_results(self):
        from quorune.rules.event_player_result_shapes import combat_damage_energy_runtime_dependencies

        for fields in ({"event": "damage.dealt"}, {"active_zone": "command"}, {"cost_schema": {}},
                       {"target_schema": {}}, {"event_condition": None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                program = self.program()
                vars(program).update(fields)
                combat_damage_energy_runtime_dependencies(program)
        for field, value in (("subjects", "each-opponent"), ("counter", "poison"), ("source", "$event.object")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                program = self.program()
                program.effects[0][field] = value
                combat_damage_energy_runtime_dependencies(program)
        for index, change in ((0, {"value": "permanent"}), (1, {"value": 1}), (2, {"value": "$source.ref"})):
            with self.subTest(field=index), self.assertRaises(ValueError):
                program = self.program()
                program.event_condition["all"][index].update(change)
                combat_damage_energy_runtime_dependencies(program)
        program = self.program()
        program.effects[0]["amount"]["coefficient"] = 2
        with self.assertRaises(ValueError):
            combat_damage_energy_runtime_dependencies(program)

    def test_complete_compiler_keeps_event_amount_and_all_existing_owners(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.event_player_result_shapes import combat_damage_energy_effect

        fixture = record(
            "Whenever this creature deals combat damage to a player, you get that many {E} (energy counters).",
            "Creature",
        )
        ir = compile_oracle_card(fixture, capability_registry=load_default_capability_registry())
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("damage.dealt.self", node.event)
        self.assertEqual((combat_damage_energy_effect(),), node.effects)
        self.assertEqual(1, node.span.line)
        self.assertTrue({
            "trigger.event.normalized_damage", "trigger.placement.apnap",
            "counter.producer.fixed_player_effect", "quantity_expression.scalar_effect_amount",
        }.issubset(node.capability_dependencies))

    def test_compiler_rejects_missing_referents_and_unrepresented_neighbor_bodies(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card

        registry = load_default_capability_registry()
        for text in (
            "Whenever this creature deals damage to a player, you get that many {E}.",
            "Whenever another creature you control deals combat damage to a player, you get that many {E}.",
            "Whenever this creature deals combat damage to a player, they get that many {E}.",
            "Whenever this creature deals combat damage to a player, you get that many {E}, then destroy all lands.",
            "When this creature enters, you get that many {E}.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Creature"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)


class CombatDamageEnergyNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        baseline = json.loads((ROOT / "tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        baseline["cards"] = [row for row in baseline["cards"] if row["name"] == "Generic Bound Plains"]
        baseline["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(baseline), encoding="utf-8")
        database = folder / "energy-damage.sqlite3"
        build_fixture_database([base, ROOT / "tests/fixtures/combat-damage-energy-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat energy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_both_complete_originals_bind_through_normal_card_program_admission(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry

        for name in ("Empyreal Voyager", "Peema Trailblazer"):
            with self.subTest(card=name):
                result = compile_best_available_card_program(
                    self.db, self.db.lookup(name, fuzzy=False), semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile="commander_review",
                )
                binding = bind_card_program_runtime(
                    result, capability_registry=self.registry, profile="commander_review",
                )
                self.assertTrue(binding["strict_capability_ready"], binding.get("blockers"))


    def test_ordered_activation_promotes_through_regular_registry_with_exact_scalar_shape(self):
        from copy import deepcopy
        from dataclasses import replace

        from quorune.compiler.public_query_effect_amounts import public_query_amount_program_is_closed

        session = self.session(120040206)
        source = self.add(session.engine, "Peema Trailblazer", zone="hand")
        program, = (
            p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == "activate"
        )
        self.assertEqual("trusted", program.trust_level)
        required = program.capability_dependencies
        self.assertTrue(public_query_amount_program_is_closed(program, required_dependencies=required))
        swapped = replace(program, effects=list(reversed(program.effects)))
        self.assertFalse(public_query_amount_program_is_closed(swapped, required_dependencies=required))
        for changed_field, changed_value in (("characteristic", "toughness"), ("reduction", "sum"),
                                             ("controller_relation", "any")):
            with self.subTest(field=changed_field):
                changed = deepcopy(program.effects)
                changed[1]["count"]["aggregate"][changed_field] = changed_value
                altered = replace(program, effects=changed)
                self.assertFalse(public_query_amount_program_is_closed(altered, required_dependencies=required))

    def paid_voyager(self, seed, *, fully_prevent=False):
        session = self.session(seed)
        engine = session.engine
        voyager = self.add(engine, "Empyreal Voyager", zone="hand")
        self.add(engine, "Fervor")
        answer = self.add(engine, "Unsummon", seat="B", zone="hand", ref="voyager-bounce")
        self.add(engine, "Island", seat="B")
        private = self.add(engine, "Unsummon", seat="C", zone="hand", ref="voyager-private")
        if fully_prevent:
            shield = self.add(engine, "Mending Hands", seat="B", zone="hand")
            self.add(engine, "Plains", seat="B")
        else:
            shield = self.add(engine, "Samite Healer", seat="B")
            shield.acquired_control_turn_count = -1
        for card in (voyager, answer, shield):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1, "G": 1, "U": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", voyager)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.attack(session, [(voyager, "B")])
        self.priority(session, "B")
        if fully_prevent:
            self.cast_for(session, "B", shield, ["B"])
        else:
            offered, = (
                row for row in session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                if row["id"].startswith("activate:" + shield.ref + ":")
            )
            result = session.act("pilot:B", {"action_id": offered["id"], "targets": ["B"], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        return session, voyager, answer, private

    def test_paid_voyager_uses_committed_damage_after_prevention_and_later_departure(self):
        session, voyager, answer, private = self.paid_voyager(120040207)
        self.until(session, lambda s: self.trigger_count(s, voyager) == 1)
        self.assertEqual(39, session.state.players["B"].life)
        self.assertEqual([0, 0, 0, 0], [session.state.players[seat].energy for seat in "ABCD"])
        self.cast_for(session, "B", answer, [voyager.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[voyager.object_id].zone)
        self.assertEqual(1, self.trigger_count(session, voyager))
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([1, 0, 0, 0], [session.state.players[seat].energy for seat in "ABCD"])
        self.replay(session, load=True)

    def test_paid_voyager_fully_prevented_combat_produces_no_trigger_or_energy(self):
        session, voyager, _answer, _private = self.paid_voyager(120040208, fully_prevent=True)
        self.until(session, lambda s: s.state.phase == "postcombat_main")
        self.assertEqual(40, session.state.players["B"].life)
        self.assertEqual(0, self.trigger_count(session, voyager))
        self.assertEqual([0, 0, 0, 0], [session.state.players[seat].energy for seat in "ABCD"])
        self.replay(session, load=True)

    def test_paid_peema_earns_energy_and_pays_its_printed_exhaust(self):
        from quorune.counter_placement import CounterPlacementRequest, place_counters

        session = self.session(120040209)
        engine = session.engine
        source = self.add(engine, "Peema Trailblazer", zone="hand")
        self.add(engine, "Fervor")
        self.assert_closed(session, source)
        place_counters(
            engine, (CounterPlacementRequest("player", "A", "energy", 3, "A"),),
            reason="Preexisting energy in the controlled starting state",
        )
        self.prepare(session, mana={"C": 2, "G": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertIsNone(self.resolve(session))
        self.attack(session, [(source, "B")])
        self.until(session, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(37, session.state.players["B"].life)
        self.assertEqual(3, session.state.players["A"].energy)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([6, 0, 0, 0], [session.state.players[seat].energy for seat in "ABCD"])
        self.priority(session, "A")
        hand_size = len(session.state.players["A"].zones["hand"])
        offered, = (
            row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith("activate:" + source.ref + ":")
        )
        result = session.act("pilot:A", {"action_id": offered["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, session.state.players["A"].energy)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"+1/+1": 2}, session.state.cards[source.object_id].counters)
        self.assertEqual(hand_size + 5, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

    def test_normal_counter_prohibition_blocks_energy_after_recognized_combat_damage(self):
        session = self.session(120040211)
        source = self.add(session.engine, "Empyreal Voyager", zone="hand")
        prohibition = self.add(session.engine, "Solemnity", seat="C")
        self.add(session.engine, "Fervor")
        self.assert_closed(session, source)
        self.assert_closed(session, prohibition)
        self.prepare(session, mana={"C": 1, "G": 1, "U": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertIsNone(self.resolve(session))
        self.attack(session, [(source, "B")])
        self.until(session, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(38, session.state.players["B"].life)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([0, 0, 0, 0], [session.state.players[seat].energy for seat in "ABCD"])
        self.replay(session, load=True)

    def test_player_counter_commit_fault_restores_exact_state_and_resumes(self):
        from unittest.mock import patch

        from quorune.errors import GameRuleError

        session, voyager, _answer, _private = self.paid_voyager(120040210)
        self.until(session, lambda s: self.trigger_count(s, voyager) == 1)
        original = session.engine.place_player_counters_intent
        committed = []

        def fault(intent):
            result = original(intent)
            committed.append(session.state.players["A"].energy)
            raise GameRuleError("Injected energy fault after counter placement")

        with patch.object(session.engine, "place_player_counters_intent", side_effect=fault):
            for _ in range(12):
                principal = session.pending_principals()[0]
                before = authoritative_state_hash(session.state)
                result = session.act(principal, {"action_id": "pass"})
                if not result.ok:
                    self.assertIn("Injected energy fault", result.summary)
                    break
            else:
                self.fail("The actual energy placement transaction was not reached")
        self.assertEqual([1], committed)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual(0, session.state.players["A"].energy)
        session = self.replay(session, load=True)
        self.assertTrue(session.act(principal, {"action_id": "pass"}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.players["A"].energy)
        self.replay(session, load=True)

    def test_wrong_prevention_amount_mutation_fails_the_actual_original_journey(self):
        from unittest.mock import patch

        from quorune.semantic_runtime import values as runtime

        original = runtime.resolve_scalar_effect_amount
        observed = []

        def ignore_prevention(host, value, item):
            amount = original(host, value, item)
            if item.kind == "triggered_ability" and value.get("origin") == "committed_event_amount":
                observed.append(amount)
                return 2
            return amount

        result = unittest.TestResult()
        with patch.object(runtime, "resolve_scalar_effect_amount", ignore_prevention):
            type(self)("test_paid_voyager_uses_committed_damage_after_prevention_and_later_departure").run(result)
        self.assertFalse(result.errors, result.errors)
        self.assertTrue(observed)
        self.assertEqual({1}, set(observed))
        self.assertEqual(1, len(result.failures))
        self.assertIn('AssertionError: Lists differ: [1, 0, 0, 0] != [2, 0, 0, 0]', result.failures[0][1])

    def test_genuine_damage_scalar_and_player_counter_withdrawals_block_both_originals(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        originals = [self.db.lookup(name) for name in ("Empyreal Voyager", "Peema Trailblazer")]

        def registry_with_verified_index(value):
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def admitted(record, registry):
            program = compile_best_available_card_program(
                self.db, record, semantic_registry=SemanticRegistry(),
                capability_registry=registry, capability_profile="commander_review",
            )
            return bind_card_program_runtime(
                program, capability_registry=registry, profile="commander_review",
            )["strict_capability_ready"]

        baseline = registry_with_verified_index(raw)
        self.assertTrue(all(admitted(card, baseline) for card in originals))
        for cap in ("trigger.event.normalized_damage", "quantity_expression.scalar_effect_amount",
                    "counter.producer.fixed_player_effect"):
            with self.subTest(capability=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Focused combat damage energy withdrawal"],
                )
                registry = registry_with_verified_index(changed)
                self.assertTrue(all(not admitted(card, registry) for card in originals))
                self.assertTrue(admitted(self.db.lookup("Isamaru, Hound of Konda"), registry))
