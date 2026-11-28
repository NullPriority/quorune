from __future__ import annotations

from dataclasses import replace
import re
import unittest
from unittest.mock import patch

from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.private_opponent_library_cast_model import (
    PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY, PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC,
    private_opponent_library_cast_selection,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.private_opponent_library_cast_shapes import (
    private_opponent_library_cast_node_capabilities, private_opponent_library_cast_target_schema,
)
from quorune.semantics import SemanticProgram


ENTRY = (
    "When Court Witness enters, look at the top four cards of target opponent's library, "
    "exile one of them face down, then put the rest on the bottom of that library in a random order. "
    "You may cast that card for as long as it remains exiled, and mana of any type can be spent to cast that spell."
)
TEXT = "Deathtouch\n" + ENTRY
PROFILE = "commander_review"


def source_record(text):
    return CardRecord(
        oracle_id="compiler:private-opponent-library", name="Court Witness, Arcane Arbiter", mana_cost="{2}{B}{B}",
        mana_value=4, type_line="Legendary Creature — Rogue", oracle_text=text, power="2", toughness="3",
        loyalty=None, defense=None, colors=("B",), color_identity=("B",), keywords=("Deathtouch",),
        produced_mana=(), layout="normal", released_at="2026-01-01", legalities={"commander": "legal"},
        faces=(), raw={},
    )


class PrivateOpponentLibraryCastCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text=TEXT):
        return compile_oracle_card(source_record(text), capability_registry=self.registry, capability_profile=PROFILE)

    def test_exact_entry_targets_one_opponent_and_keeps_the_complete_private_partition(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        keyword, node = ir.faces[0].nodes
        self.assertTrue(keyword.exact)
        self.assertEqual(ENTRY, TEXT[node.span.start:node.span.end])
        self.assertEqual(("battlefield", "permanent.enter.self"), (node.active_zone, node.event))
        self.assertIsNone(node.event_condition)
        self.assertEqual(private_opponent_library_cast_target_schema(), node.target_schema)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, node.runtime_coverage)
        self.assertIn(PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY, node.capability_dependencies)
        self.assertIn("target.revalidate_resolution", node.capability_dependencies)
        effect, = node.effects
        self.assertEqual(private_opponent_library_cast_selection(), effect)
        self.assertEqual((4, 1, 1), (effect["look_count"], effect["minimum"], effect["maximum"]))
        self.assertEqual("$target.0", effect["player"])
        self.assertEqual("exile_face_down_cast", effect["selected_destination"])
        self.assertEqual(("library_bottom", "random"), (effect["remainder_destination"], effect["remainder_order"]))
        self.assertIs(effect["public_reveal"], False)
        self.assertIs(effect["selected_reveal"], False)

    def test_nearby_public_look_search_target_and_permission_variants_stay_outside_the_family(self):
        for text in (
            ENTRY.replace("target opponent", "target player"),
            ENTRY.replace("of target opponent's library", "of your library"),
            ENTRY.replace("look at the top four cards", "search the top four cards"),
            ENTRY.replace("exile one of them face down", "exile one of them face up"),
            ENTRY.replace("top four", "top three"),
            ENTRY.replace("a random order", "any order"),
            ENTRY.replace("for as long as it remains exiled", "until end of turn"),
            ENTRY.replace("mana of any type", "mana of any color"),
            ENTRY.replace("Court Witness enters", "Court Witness dies"),
            ENTRY.replace("Court Witness enters", "another creature enters"),
        ):
            with self.subTest(text=text):
                ir = self.compile("Deathtouch\n" + text)
                self.assertFalse(any(PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY in node.capability_dependencies
                                     for face in ir.faces for node in face.nodes if node.exact))

    def test_runtime_authority_requires_the_original_entry_target_and_fine_capability(self):
        _, node = self.compile().faces[0].nodes
        dependencies = list(node.capability_dependencies)
        program = SemanticProgram(
            key="compiler:private-opponent-library", label="Private opponent library effect", ability_id="trigger:front:n2",
            active_zone="battlefield", event="permanent.enter.self", effects=list(node.effects),
            target_schema=dict(node.target_schema), coverage=[*node.mechanics, *node.runtime_coverage],
            capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict(),
        )
        result = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(result["strict"], result)

        inherited = [value for value in dependencies if value != PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY]
        unbound = replace(program, coverage=[CURRENT_ABILITY_FRAGMENT_COVERAGE], capability_dependencies=inherited,
                          capability_closure=self.registry.closure(inherited, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY, result["blockers"])

        effect, = program.effects
        for changed in (
            replace(program, event="permanent.enter"),
            replace(program, active_zone="graveyard"),
            replace(program, target_schema=None),
            replace(program, target_schema={**node.target_schema, "player_relation": "any"}),
            replace(program, coverage=list(node.mechanics)),
            replace(program, effects=[{**effect, "player": "$controller"}]),
            replace(program, effects=[{**effect, "remainder_order": "chosen"}]),
            replace(program, effects=[{**effect, "schema_version": 25.0}]),
        ):
            with self.subTest(program=changed):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        self.assertFalse(private_opponent_library_cast_node_capabilities(
            effects=program.effects, target_schema={**node.target_schema, "count": True},
            mechanic_ids=(PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC,),
        ))

    def test_compiler_omission_leaves_the_independent_keyword_and_an_unresolved_trigger(self):
        self.assertEqual("exact", self.compile().status)
        regex = "quorune.compiler.private_opponent_library_cast_templates._BODY"
        with patch(regex, re.compile("omitted for a focused mutation")):
            ir = self.compile()
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.faces[0].nodes[0].exact)
        self.assertTrue(ir.material_residuals)
