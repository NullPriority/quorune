from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardRecord
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiler.public_zone_program_closure import is_closed_reanimation_effect_program
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.ordered_graveyard_reanimation import (
    ORDERED_GRAVEYARD_REANIMATION_CAPABILITY, ORDERED_GRAVEYARD_REANIMATION_MECHANIC,
    ordered_graveyard_reanimation_effect,
)
from quorune.semantics import SemanticProgram


TEXT = (
    "Return the top creature card of your graveyard to the battlefield. "
    "That creature gains haste until end of turn. "
    "Exile it at the beginning of the next end step."
)
TARGETED = TEXT.replace("the top creature card of", "target legendary creature card from")
DIES = "When this creature dies, exile it, then return the top creature card of your graveyard to the battlefield."
PROFILE = "commander_review"


def source_record(text, type_line="Instant", *, keywords=()):
    return CardRecord(
        oracle_id="compiler:ordered-graveyard-return", name="Ordered Return Fixture",
        mana_cost="{1}{B}", mana_value=2, type_line=type_line, oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None,
        colors=("B",), color_identity=("B",), keywords=keywords, produced_mana=(),
        layout="normal", released_at="2026-01-01", legalities={"commander": "legal"}, faces=(), raw={},
    )


class OrderedGraveyardReanimationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text=TEXT, *, registry=None, type_line="Instant", keywords=()):
        return compile_oracle_card(
            source_record(text, type_line, keywords=keywords),
            capability_registry=registry or self.registry, capability_profile=PROFILE,
        )

    def test_exact_ordered_effect_preserves_the_span_and_has_no_target(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(TEXT, TEXT[node.span.start:node.span.end])
        self.assertEqual(("stack", "resolve"), (node.active_zone, node.event))
        self.assertIsNone(node.target_schema)
        self.assertEqual((ordered_graveyard_reanimation_effect(),), node.effects)
        self.assertIn(ORDERED_GRAVEYARD_REANIMATION_MECHANIC, node.mechanics)
        self.assertLessEqual({
            ORDERED_GRAVEYARD_REANIMATION_CAPABILITY, "zone.graveyard.ordered_simultaneous_arrival",
            "combat.attack.haste", "activation.tap_untap_cost.haste", "trigger.delayed.fixed_object_action",
            "continuous.resolution.fixed_characteristics_until_end_of_turn", "zone.change.destination_replacement",
        }, set(node.capability_dependencies))
        self.assertNotIn("target.revalidate_resolution", node.capability_dependencies)

    def test_same_instruction_preserves_the_closure_of_an_independent_fixed_buyback(self):
        text = (
            "Buyback {2} (You may pay an additional {2} as you cast this spell. "
            "If you do, put this card into your hand as it resolves.)\n" + TEXT
        )
        ir = self.compile(text, keywords=("Buyback",))
        self.assertEqual("exact", ir.status, ir.material_residuals)
        buyback, ordered = ir.faces[0].nodes
        self.assertIn("buyback", buyback.mechanics)
        self.assertEqual(TEXT, text[ordered.span.start:ordered.span.end])
        self.assertEqual((ordered_graveyard_reanimation_effect(),), ordered.effects)
        self.assertIsNone(ordered.target_schema)

    def test_mistmoon_style_dies_and_other_near_neighbors_remain_outside_ordered_admission(self):
        for text, type_line in (
            (DIES, "Creature"), (TEXT, "Enchantment"),
            (TEXT.replace("your graveyard", "an opponent's graveyard"), "Instant"),
            (TEXT.replace("top creature card", "bottom creature card"), "Instant"),
            (TEXT.replace("Return the", "You may return the"), "Instant"),
            (TEXT.replace("gains haste until end of turn", "gains haste"), "Instant"),
            (TEXT.replace("the next end step", "your next end step"), "Instant"),
            (TEXT.replace("Exile it", "Sacrifice it"), "Instant"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(any(ORDERED_GRAVEYARD_REANIMATION_CAPABILITY in node.capability_dependencies
                                     for face in ir.faces for node in face.nodes))
        targeted = self.compile(TARGETED)
        self.assertEqual("exact", targeted.status, targeted.material_residuals)
        node, = targeted.faces[0].nodes
        self.assertIsNotNone(node.target_schema)
        self.assertNotIn(ORDERED_GRAVEYARD_REANIMATION_CAPABILITY, node.capability_dependencies)

    def test_resolving_spell_and_runtime_dependency_are_inferred_from_the_descriptor(self):
        node, = self.compile().faces[0].nodes
        program = SemanticProgram(
            key="compiler:ordered", label="Ordered graveyard return", effects=list(node.effects),
            coverage=list(node.mechanics), capability_dependencies=list(node.capability_dependencies),
            capability_closure=self.registry.closure(node.capability_dependencies, profile=PROFILE).to_dict(),
        )
        self.assertTrue(is_closed_reanimation_effect_program(program))
        result = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(result["strict"], result)

        dependencies = [value for value in node.capability_dependencies if value != ORDERED_GRAVEYARD_REANIMATION_CAPABILITY]
        stripped = replace(program, coverage=[], capability_dependencies=dependencies,
                           capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + ORDERED_GRAVEYARD_REANIMATION_CAPABILITY,
                      result["blockers"])
        effect, = program.effects
        for invalid in (
            replace(program, event="activate", active_zone="battlefield"),
            replace(program, target_schema={"zones": ["graveyard"], "categories": ["card"], "count": 1}),
            replace(program, effects=[{**effect, "selection": "controller_graveyard_top_card"}]),
            replace(program, effects=[{**effect, "card": "$target.0"}]),
            replace(program, effects=[{**effect, "schema_version": 20.0}]),
            replace(program, effects=[{**effect, "followup": {**effect["followup"], "end_step": "controllers_next"}}]),
        ):
            with self.subTest(program=invalid):
                self.assertFalse(is_closed_reanimation_effect_program(invalid))
                result = bind_semantic_program_runtime(invalid, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

    def test_ordered_dependency_withdrawal_and_compiler_omission_fail_closed(self):
        self.assertEqual("exact", self.compile().status)
        owner = "quorune.compiler.ordered_graveyard_reanimation_templates._ORDERED_RETURN"
        with patch(owner, "omitted for the focused mutation"):
            mutated = self.compile()
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(value for value in raw["capabilities"] if value["id"] == "zone.graveyard.ordered_simultaneous_arrival")
        row.update(status="blocked", blockers=["withdrawn for the ordered graveyard dependency witness"])
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        attempted = self.compile(registry=registry)
        self.assertNotEqual("exact", attempted.status)
        self.assertTrue(any("zone.graveyard.ordered_simultaneous_arrival" in blocker
                            for residual in attempted.material_residuals for blocker in residual.blockers))
        self.assertEqual("exact", self.compile(TARGETED, registry=registry).status)
