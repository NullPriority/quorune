from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest

from quorune.carddb import CardRecord
from quorune.compiler.target_permanent_shuffle_reveal_templates import target_permanent_shuffle_reveal_template
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.closed_effect_program_shapes import closed_effect_program_runtime_dependencies
from quorune.rules.library_result_capability_shapes import (
    is_closed_target_permanent_shuffle_reveal_program,
    library_result_payload_dependencies,
    target_permanent_shuffle_reveal_node_capabilities,
)
from quorune.target_controller_information import (
    TARGET_OWNER_INFORMATION_CAPABILITY,
    TARGET_OWNER_INFORMATION_MECHANIC,
    target_owner_reference,
)
from quorune.target_permanent_shuffle_reveal_model import WARP_CAPABILITY, WARP_MECHANIC, WARP_OPERATION
from quorune.semantics import SemanticProgram


WARP = (
    "The owner of target permanent shuffles it into their library, then reveals the top card of their library. "
    "If it's a permanent card, they put it onto the battlefield."
)
SCHEMA = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}


def spell(text):
    return CardRecord(
        oracle_id="07a0cba9-8768-4fd9-a3d5-b0f83b4bf8e8", name="Chaos Warp", mana_cost="{2}{R}",
        mana_value=3, type_line="Instant", oracle_text=text, power=None, toughness=None, loyalty=None,
        defense=None, colors=("R",), color_identity=("R",), keywords=(), produced_mana=(), layout="normal",
        released_at="2011-06-17", legalities={"commander": "legal"}, faces=(), raw={},
    )


class TargetPermanentShuffleRevealCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text):
        return compile_oracle_card(spell(text), capability_registry=self.registry, capability_profile="commander_review")

    def test_complete_printed_spell_lowers_one_mandatory_permanent_target(self):
        result = self.compile(WARP)
        nodes = [node for face in result.faces for node in face.nodes]
        self.assertEqual(1, len(nodes))
        node, = nodes
        self.assertEqual("target-permanent-shuffle-reveal-v1", node.template_id)
        self.assertEqual(("stack", "resolve"), (node.active_zone, node.event))
        self.assertEqual(SCHEMA, node.target_schema)
        self.assertEqual(
            (
                {"op": "shuffle_into_library", "card": "$target.0"},
                {"op": WARP_OPERATION, "schema_version": 1, "player": target_owner_reference(0)},
            ),
            node.effects,
        )
        self.assertIn(WARP_MECHANIC, node.mechanics)
        self.assertIn(TARGET_OWNER_INFORMATION_MECHANIC, node.mechanics)
        self.assertIn(WARP_CAPABILITY, node.capability_dependencies)
        self.assertIn(TARGET_OWNER_INFORMATION_CAPABILITY, node.capability_dependencies)
        self.assertIn("zone.single_owner_move.fixed_destination", node.capability_dependencies)
        self.assertIn("target.revalidate_resolution", node.capability_dependencies)

    def test_nearby_or_partially_matched_library_results_remain_outside_the_family(self):
        alternatives = (
            WARP.replace("target permanent", "target nonland permanent"),
            WARP.replace("owner", "controller", 1),
            WARP.replace("reveals the top card", "looks at the top card"),
            WARP.replace("If it's a permanent card", "If it's a creature card"),
            WARP.replace("onto the battlefield", "into their hand"),
            WARP + " Draw a card.",
            WARP + "\nDraw a card.",
            "Draw a card.\n" + WARP,
            "Target player shuffles their library.",
            "The owner of target nonland permanent shuffles it into their library, then draws two cards.",
        )
        for text in alternatives:
            with self.subTest(text=text):
                self.assertIsNone(target_permanent_shuffle_reveal_template(text))
                result = self.compile(text)
                self.assertFalse(any(
                    node.template_id == "target-permanent-shuffle-reveal-v1"
                    for face in result.faces for node in face.nodes
                ))

    def test_strict_shape_refuses_missing_targets_malformed_payloads_and_other_events(self):
        _, effects, schema, mechanics = target_permanent_shuffle_reveal_template(WARP)
        shuffle, reveal = effects
        payload = (WARP_CAPABILITY, TARGET_OWNER_INFORMATION_CAPABILITY)
        required = (*payload, "zone.single_owner_move.fixed_destination", "target.revalidate_resolution")
        self.assertEqual(payload, library_result_payload_dependencies(reveal))
        self.assertEqual(required, target_permanent_shuffle_reveal_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        program = SimpleNamespace(
            effects=effects, target_schema=schema, coverage=mechanics, active_zone="stack", event="resolve",
            capability_dependencies=required, ability_id="spell:front", cost_schema=None,
            event_condition=None, handlers=(),
        )
        self.assertTrue(is_closed_target_permanent_shuffle_reveal_program(program))
        for update in (
            {"capability_dependencies": (WARP_CAPABILITY,)},
            {"capability_dependencies": (*payload, "target.revalidate_resolution")},
            {"coverage": (WARP_MECHANIC,)},
            {"coverage": (WARP_MECHANIC, "cr-115-targets")},
            {"effects": effects * 2},
            {"effects": effects[::-1]},
            {"effects": (reveal,)},
            {"effects": (shuffle | {"card": "$target.1"}, reveal)},
            {"effects": (shuffle, {"op": "reveal_top_permanent", "player": "$target.owner.0"})},
            {"target_schema": None},
            {"target_schema": schema | {"count": True}},
            {"target_schema": schema | {"types_none": ["land"]}},
            {"event": "activate"},
            {"active_zone": "battlefield"},
        ):
            self.assertFalse(is_closed_target_permanent_shuffle_reveal_program(SimpleNamespace(**(vars(program) | update))))
        for update in (
            {"schema_version": True},
            {"schema_version": 2},
            {"card": "$target.0"},
            {"player": "B"},
            {"player": "$target.owner.0"},
            {"player": target_owner_reference(1)},
            {"owner": "B"},
        ):
            with self.subTest(update=update):
                malformed = reveal | update
                self.assertEqual((), target_permanent_shuffle_reveal_node_capabilities(
                    effects=(shuffle, malformed), target_schema=schema, mechanic_ids=mechanics,
                ))
                with self.assertRaises(ValueError):
                    library_result_payload_dependencies(malformed)

    def test_runtime_dependencies_preserve_warp_and_existing_shuffle_draw_owners(self):
        def program_for(text):
            face, = self.compile(text).faces
            node, = face.nodes
            closure = self.registry.closure(node.capability_dependencies, profile="commander_review")
            return SemanticProgram(
                key="spell-probe", label="Spell probe", ability_id="spell:front",
                effects=list(node.effects), target_schema=node.target_schema,
                coverage=list(node.mechanics), capability_dependencies=list(node.capability_dependencies),
                capability_closure=closure.to_dict(), provenance={"template_id": node.template_id},
            )

        warp = program_for(WARP)
        required = set(closed_effect_program_runtime_dependencies(warp))
        self.assertEqual({
            WARP_CAPABILITY, TARGET_OWNER_INFORMATION_CAPABILITY,
            "zone.single_owner_move.fixed_destination", "target.revalidate_resolution",
        }, required)
        for mutation in (
            replace(warp, effects=warp.effects[::-1]),
            replace(warp, effects=[*warp.effects, {"op": "draw", "player": "$controller", "count": 1}]),
            replace(warp, effects=[warp.effects[0], {"op": "reveal_top_permanent", "player": "$target.owner.0"}]),
            replace(warp, cost_schema={"optional_costs": []}),
        ):
            with self.assertRaisesRegex(ValueError, "complete ordinary spell recipe"):
                closed_effect_program_runtime_dependencies(mutation)

        draw = program_for("The owner of target nonland permanent shuffles it into their library, then draws two cards.")
        draw_required = closed_effect_program_runtime_dependencies(draw)
        self.assertIn("resolution.effect_program.target_recipient_result", draw_required)
        self.assertNotIn(WARP_CAPABILITY, draw_required)
