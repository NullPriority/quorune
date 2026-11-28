from __future__ import annotations

from dataclasses import replace
import json
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_to_dict
from quorune.aura import parse_enchant_line
from quorune.card_programs.admission import CARD_PROGRAM_ADMISSION_FIELD, REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.enchanted_departure_events import (
    ENCHANTED_DEPARTURE_EVENT,
    enchanted_departure_condition, enchanted_departure_context, enchanted_departure_observer_context,
    enchanted_departure_return_node_capabilities,
)
from quorune.rules.enchanted_departure_return import (
    ENCHANTED_DEPARTURE_ARRIVALS, ENCHANTED_DEPARTURE_OBSERVER, ENCHANTED_DEPARTURE_RETURN_CAPABILITY,
    ENCHANTED_DEPARTURE_SCOPE, enchanted_departure_return_effect,
)
from quorune.semantics import SemanticProgram


ENCHANT = "Enchant creature or planeswalker you control"
TRIGGER = (
    "When enchanted permanent dies or is put into exile, "
    "return that card to the battlefield under your control."
)
TEXT = ENCHANT + "\n" + TRIGGER
PROFILE = "commander_review"


def source_record(text):
    return CardRecord(
        oracle_id="compiler:enchanted-departure-return", name="Enchanted Departure Fixture",
        mana_cost="{B}", mana_value=1, type_line="Enchantment — Aura", oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=("B",),
        color_identity=("B",), keywords=("Enchant",), produced_mana=(), layout="normal",
        released_at="2026-01-01", legalities={"commander": "legal"}, faces=(), raw={},
    )


class EnchantedDepartureReturnCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text=TEXT, *, registry=None):
        return compile_oracle_card(
            source_record(text), capability_registry=registry or self.registry, capability_profile=PROFILE,
        )

    def test_exact_typed_enchant_and_one_nontargeted_death_or_exile_return(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        enchant, trigger = ir.faces[0].nodes
        spec = parse_enchant_line(ENCHANT)
        handler, = enchant.handlers
        self.assertEqual(ability_fragment_to_dict(spec), handler["fragment"])
        self.assertEqual(("creature", "planeswalker"), spec.types_any)
        self.assertEqual("you", spec.controller_relation.value)
        self.assertIn("attachment.aura.typed_restriction", enchant.capability_dependencies)
        self.assertEqual(TRIGGER, TEXT[trigger.span.start:trigger.span.end])
        self.assertEqual(("battlefield", ENCHANTED_DEPARTURE_EVENT), (trigger.active_zone, trigger.event))
        self.assertEqual(enchanted_departure_condition(), trigger.event_condition)
        self.assertEqual((enchanted_departure_return_effect(),), trigger.effects)
        self.assertIsNone(trigger.target_schema)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, trigger.runtime_coverage)
        self.assertLessEqual(set(enchanted_departure_return_node_capabilities(trigger.effects, None)),
                             set(trigger.capability_dependencies))
        self.assertNotIn("target.revalidate_resolution", trigger.capability_dependencies)

    def test_different_attachment_controller_types_and_return_wording_stay_outside_the_contract(self):
        for source in (
            ENCHANT.removesuffix(" you control"), "Enchant creature you control",
            "Enchant artifact or creature you control", "Enchant permanent you control", "",
            ENCHANT + "\nEnchant permanent",
        ):
            with self.subTest(source=source):
                ir = self.compile((source + "\n" if source else "") + TRIGGER)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(any(residual.kind == "trigger_source_restriction" for residual in ir.material_residuals))
        for body in (
            TRIGGER.replace("enchanted permanent", "enchanted creature"),
            TRIGGER.replace("under your control", "under its owner's control"),
            TRIGGER.replace("or is put into exile", "or is returned to a hand"),
            TRIGGER.replace("return that card", "you may return that card"),
        ):
            with self.subTest(body=body):
                ir = self.compile(ENCHANT + "\n" + body)
                self.assertFalse(any(
                    ENCHANTED_DEPARTURE_RETURN_CAPABILITY in node.capability_dependencies
                    for face in ir.faces for node in face.nodes if node.exact
                ))

    def test_runtime_gate_infers_the_exact_effect_and_requires_complete_enchant_source(self):
        _, node = self.compile().faces[0].nodes
        dependencies = list(node.capability_dependencies)
        records = {
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
            CARD_PROGRAM_ADMISSION_FIELD: {"schema_version": 1, "oracle_ir_status": "exact", "material_residual_count": 0},
        }
        program = SemanticProgram(
            key="compiler:enchanted-departure", label="Enchanted departure", ability_id="trigger:front:n2",
            active_zone="battlefield", event=ENCHANTED_DEPARTURE_EVENT, event_condition=dict(node.event_condition),
            effects=list(node.effects), coverage=[*node.mechanics, *node.runtime_coverage], provenance=records,
            capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict(),
        )
        result = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(result["strict"], result)
        stripped = [value for value in dependencies if value != ENCHANTED_DEPARTURE_RETURN_CAPABILITY]
        changed = replace(program, capability_dependencies=stripped,
                          capability_closure=self.registry.closure(stripped, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + ENCHANTED_DEPARTURE_RETURN_CAPABILITY,
                      result["blockers"])
        for changed in (
            replace(program, provenance={}), replace(program, event="creature.dies"),
            replace(program, target_schema={"zones": ["graveyard"], "categories": ["card"], "count": 1}),
            replace(program, event_condition={"field": "to", "op": "eq", "value": "exile"}),
            replace(program, effects=[{**node.effects[0], "schema_version": 22.0}]),
            replace(program, coverage=list(node.mechanics)),
        ):
            with self.subTest(program=changed):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

    def test_departure_context_captures_public_arrivals_and_only_the_original_attachment(self):
        program = SimpleNamespace(capability_dependencies=(ENCHANTED_DEPARTURE_RETURN_CAPABILITY,))
        host = SimpleNamespace(semantics=SimpleNamespace(programs_for_event=lambda _event: (program,)))
        event = {"attachments": ["aura-ref"], "card": "departed-ref", "card_object_identity": "departed@2",
                 "types": ["artifact"], "controller": "C"}
        departed = SimpleNamespace(object_id="departed", card_ref="departed-ref", owner="A", composition=None,
                                   previous_logical_object_id="departed@2", zone_change_counter=3,
                                   origin="battlefield", destination="exile", previous_attachments=("aura-ref",),
                                   token=False, card_object=True)
        captured = enchanted_departure_context(host, event, departed, ())
        self.assertEqual({"object_id": "departed", "reference": "departed-ref",
                          "previous_logical_object_id": "departed@2"}, captured[ENCHANTED_DEPARTURE_SCOPE])
        self.assertEqual([{"object_id": "departed", "reference": "departed-ref",
                          "zone_change_counter": 3, "owner": "A", "zone": "exile"}], captured[ENCHANTED_DEPARTURE_ARRIVALS])
        aura = SimpleNamespace(object_id="aura", logical_object_id="aura@5", ref="aura-ref",
                               controller="B", attached_to="departed")
        observer = enchanted_departure_observer_context(aura, program, ENCHANTED_DEPARTURE_EVENT, captured)
        self.assertEqual({"object_id": "aura", "logical_object_id": "aura@5", "reference": "aura-ref",
                          "controller": "B"}, observer[ENCHANTED_DEPARTURE_OBSERVER])
        stale = SimpleNamespace(**{**vars(aura), "attached_to": "another"})
        with self.assertRaises(ValueError):
            enchanted_departure_observer_context(stale, program, ENCHANTED_DEPARTURE_EVENT, captured)
        token = SimpleNamespace(**{**vars(departed), "token": True, "destination": "graveyard"})
        self.assertEqual([], enchanted_departure_context(host, event, token, ())[ENCHANTED_DEPARTURE_ARRIVALS])
        hand = SimpleNamespace(**{**vars(departed), "destination": "hand"})
        self.assertEqual(event, enchanted_departure_context(host, event, hand, ()))

    def test_compiler_omission_and_typed_enchant_dependency_withdrawal_fail_closed(self):
        self.assertEqual("exact", self.compile().status)
        pattern = "quorune.compiler.enchanted_departure_return_templates._TRIGGER"
        with patch(pattern, re.compile("omitted for a focused mutation")):
            self.assertTrue(self.compile().material_residuals)

        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(value for value in raw["capabilities"] if value["id"] == "attachment.aura.typed_restriction")
        row.update(status="blocked", blockers=["withdrawn for enchanted departure evidence"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        ir = self.compile(registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any("attachment.aura.typed_restriction" in blocker
                            for residual in ir.material_residuals for blocker in residual.blockers))
