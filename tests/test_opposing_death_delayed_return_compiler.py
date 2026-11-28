from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.death_battlefield_return import (
    DEATH_BATTLEFIELD_ARRIVALS, death_battlefield_return_context, is_death_battlefield_return,
)
from quorune.rules.opposing_death_delayed_return import (
    OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY, OPPOSING_DEATH_DELAYED_RETURN_DEPENDENCIES,
    OPPOSING_DEATH_OBSERVER_CONTROLLER, _sealed_death_source,
    opposing_creature_death_condition, opposing_death_delayed_return_effect,
    validate_opposing_death_delayed_return,
)
from quorune.semantics import SemanticProgram


TEXT = (
    "Whenever a creature you don't control dies, return it to the battlefield under your control "
    "with an additional +1/+1 counter on it at the beginning of the next end step. "
    "That creature is a black Zombie in addition to its other colors and types."
)
PROFILE = "commander_review"


def source_record(text):
    return CardRecord(
        oracle_id="compiler:opposing-death-return", name="Opposing Death Fixture",
        mana_cost="{5}{B}{B}", mana_value=7, type_line="Enchantment", oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=("B",),
        color_identity=("B",), keywords=(), produced_mana=(), layout="normal",
        released_at="2026-01-01", legalities={"commander": "legal"}, faces=(), raw={},
    )


class OpposingDeathDelayedReturnCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text=TEXT, *, registry=None):
        return compile_oracle_card(
            source_record(text), capability_registry=registry or self.registry, capability_profile=PROFILE,
        )

    def test_complete_trigger_uses_previous_battlefield_controller_and_no_target(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(TEXT, TEXT[node.span.start:node.span.end])
        self.assertEqual(("battlefield", "creature.dies"), (node.active_zone, node.event))
        self.assertEqual(opposing_creature_death_condition(), node.event_condition)
        self.assertEqual((opposing_death_delayed_return_effect(),), node.effects)
        self.assertIsNone(node.target_schema)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, node.runtime_coverage)
        self.assertLessEqual(set(OPPOSING_DEATH_DELAYED_RETURN_DEPENDENCIES), set(node.capability_dependencies))
        self.assertNotIn("target.revalidate_resolution", node.capability_dependencies)
        self.assertFalse(is_death_battlefield_return(node.effects[0]))

    def test_near_neighbors_keep_control_timing_and_characteristics_closed(self):
        for text in (
            TEXT.replace("you don't control", "you control"),
            TEXT.replace("a creature", "a nontoken creature", 1),
            TEXT.replace("under your control", "under its owner's control"),
            TEXT.replace("with an additional +1/+1", "with two +1/+1"),
            TEXT.replace("the next end step", "your next end step"),
            TEXT.replace("in addition to its other colors and types", "instead of its other colors and types"),
            TEXT.replace("dies, return it", "dies, you may return it"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertFalse(any(
                    OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY in node.capability_dependencies
                    for face in ir.faces for node in face.nodes if node.exact
                ))

    def test_runtime_binding_requires_the_exact_death_envelope_and_declared_dependency(self):
        node, = self.compile().faces[0].nodes
        dependencies = list(node.capability_dependencies)
        program = SemanticProgram(
            key="compiler:opposing-death", label="Opposing death",
            ability_id="trigger:front:n1", active_zone="battlefield", event="creature.dies",
            event_condition=dict(node.event_condition), effects=list(node.effects),
            coverage=[*node.mechanics, *node.runtime_coverage],
            capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict(),
        )
        strict = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(strict["strict"], strict)
        stripped = [value for value in dependencies if value != OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY]
        changed = replace(program, capability_dependencies=stripped,
                          capability_closure=self.registry.closure(stripped, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY,
                      result["blockers"])
        for changed in (
            replace(program, event_condition={"field": "owner", "op": "ne", "value": "$source.controller"}),
            replace(program, active_zone="graveyard"),
            replace(program, event="permanent.leave"),
            replace(program, coverage=list(node.mechanics)),
            replace(program, target_schema={"zones": ["graveyard"], "categories": ["card"], "count": 1}),
            replace(program, effects=[{**node.effects[0], "phase": "return"}]),
            replace(program, effects=[{**node.effects[0], "schema_version": 21.0}]),
        ):
            with self.subTest(program=changed):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

        host = SimpleNamespace(semantics=SimpleNamespace(get=lambda _key: program))
        facts = {"event": "creature.dies", "from": "battlefield", "to": "graveyard",
                 "previous_controller": "B", "controller": "B", "types": ["creature"],
                 OPPOSING_DEATH_OBSERVER_CONTROLLER: "A", DEATH_BATTLEFIELD_ARRIVALS: []}
        original = SimpleNamespace(semantic_key=program.key, controller="A", context=facts)
        _sealed_death_source(host, original, ())
        wrong_observer = {**facts, OPPOSING_DEATH_OBSERVER_CONTROLLER: "B"}
        with self.assertRaises(ValueError):
            _sealed_death_source(host, SimpleNamespace(**{**vars(original), "context": wrong_observer}), ())
        different_actor = {**vars(original), "controller": "B"}
        with self.assertRaises(ValueError):
            _sealed_death_source(host, SimpleNamespace(**different_actor), ())
        copied = SimpleNamespace(**{**different_actor, "context": {**facts, "copied_from_stack": "S1"}})
        _sealed_death_source(host, copied, ())

    def test_death_capture_seals_committed_physical_rows_and_keeps_token_occurrence(self):
        program = SimpleNamespace(capability_dependencies=(OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY,))
        host = SimpleNamespace(semantics=SimpleNamespace(programs_for_event=lambda _event: (program,)))
        facts = {"controller": "B", "previous_controller": "B", "token": False}
        original = SimpleNamespace(object_id="former", card_ref="C1", owner="A", composition=None,
                                   zone_change_counter=3, destination="graveyard", token=False, card_object=True)
        simple = death_battlefield_return_context(host, facts, original, ())
        self.assertEqual([{"object_id": "former", "reference": "C1", "owner": "A", "zone_change_counter": 3}],
                         simple[DEATH_BATTLEFIELD_ARRIVALS])
        token = SimpleNamespace(**{**vars(original), "token": True})
        self.assertEqual({**facts, "token": True, DEATH_BATTLEFIELD_ARRIVALS: []},
                         death_battlefield_return_context(host, {**facts, "token": True}, token, ()))
        parent = SimpleNamespace(permanent_departure=True, member_count=2, parent="group")
        part = SimpleNamespace(permanent_departure=False, physical_arrival=True, parent="group")
        parts = (
            SimpleNamespace(**{**vars(original), "composition": part}),
            SimpleNamespace(**{**vars(original), "object_id": "away", "card_ref": "C2",
                               "composition": part, "destination": "exile"}),
        )
        group = SimpleNamespace(**{**vars(original), "composition": parent})
        self.assertEqual(simple, death_battlefield_return_context(host, facts, group, parts))

    def test_saved_return_only_accepts_its_own_arrival_and_phase(self):
        row = {"object_id": "original", "reference": "C", "owner": "B", "zone_change_counter": 2}
        materialized = {**opposing_death_delayed_return_effect(),
                        "controller": "A", DEATH_BATTLEFIELD_ARRIVALS: [row]}
        self.assertEqual(1, len(validate_opposing_death_delayed_return(materialized)))
        with self.assertRaises(ValueError):
            validate_opposing_death_delayed_return(materialized, required_phase="return")
        returning = {**materialized, "phase": "return", "_aura_target_refs": {"original": "target"}}
        self.assertEqual(1, len(validate_opposing_death_delayed_return(returning, required_phase="return")))
        for invalid in (
            {**returning, "_aura_target_refs": {"unrelated": "target"}},
            {**materialized, DEATH_BATTLEFIELD_ARRIVALS: [row, row]},
            {**materialized, "controller": "$controller"},
        ):
            with self.subTest(effect=invalid), self.assertRaises(ValueError):
                validate_opposing_death_delayed_return(invalid)

    def test_compiler_omission_and_entry_counter_dependency_withdrawal_fail_closed(self):
        self.assertEqual("exact", self.compile().status)
        lexeme = "quorune.compiler.opposing_death_delayed_return_templates._BODY"
        with patch(lexeme, "omitted for a focused mutation"):
            self.assertTrue(self.compile().material_residuals)

        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(value for value in raw["capabilities"] if value["id"] == "counter.producer.effect_entry")
        row.update(status="blocked", blockers=["withdrawn for opposing death evidence"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        ir = self.compile(registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any("counter.producer.effect_entry" in blocker
                            for residual in ir.material_residuals for blocker in residual.blockers))
