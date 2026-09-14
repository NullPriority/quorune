from __future__ import annotations

import unittest
from unittest import mock

from common import keep_all, load_assets, make_session

from quorune.engine import CommanderEngine
from quorune.replacement import DredgeDraw, MultiplyAmount
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import (
    DREDGE_HANDLER_ID,
    DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
    DRAW_RESULT_MULTIPLIER_HANDLER_ID,
    default_draw_replacement_registry,
    DredgeReplacementHandler,
    DrawInstructionMultiplierHandler,
    DrawInstructionReplacementSourceContext,
    DrawReplacementSourceContext,
)
from quorune.semantic_runtime.context import SemanticNodeError


def descriptor(count: int = 3):
    return {
        "handler_id": DREDGE_HANDLER_ID,
        "schema_version": 1,
        "event": "draw",
        "modification": {"mill_count": count},
    }


class DrawReplacementComponentTests(unittest.TestCase):
    def test_draw_instruction_multiplier_lowers_to_typed_operation(self):
        effect = DrawInstructionMultiplierHandler().replacement_effect(
            {
                "handler_id": DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
                "schema_version": 1,
                "event": "draw.instruction",
                "condition": {
                    "affected_player_relation": "source_controller",
                },
                "modification": {"factor": 2},
            },
            DrawInstructionReplacementSourceContext(
                source_ref="A11",
                source_object_id="object:A11",
                source_zone_change_counter=2,
                source_controller="A",
                component_id="program:0",
            ),
        )

        self.assertEqual("draw.instruction", effect.event_kind)
        self.assertEqual({"eq": "A"}, effect.conditions["affected_player"])
        self.assertEqual(1, len(effect.operations))
        self.assertIsInstance(effect.operations[0], MultiplyAmount)
        self.assertEqual(2, effect.operations[0].factor)

    def test_draw_instruction_multiplier_rejects_nonclosed_shapes(self):
        handler = DrawInstructionMultiplierHandler()
        value = {
            "handler_id": DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
            "schema_version": 1,
            "event": "draw.instruction",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"factor": 2},
        }
        value["modification"]["factor"] = 3
        with self.assertRaisesRegex(SemanticNodeError, "integer 2"):
            handler.validate(value)

        value["modification"] = {"factor": 2, "unknown": True}
        with self.assertRaisesRegex(SemanticNodeError, "unknown"):
            handler.validate(value)

    def test_dredge_descriptor_lowers_to_one_optional_typed_effect(self):
        context = DrawReplacementSourceContext(
            source_ref="A17",
            source_object_id="object:A17",
            source_zone_change_counter=4,
            source_owner="A",
            component_id="program:0",
        )
        effect = DredgeReplacementHandler().replacement_effect(
            descriptor(), context
        )

        self.assertTrue(effect.optional)
        self.assertEqual("draw", effect.event_kind)
        self.assertEqual({"eq": "A"}, effect.conditions["affected_player"])
        self.assertEqual({"gte": 3}, effect.conditions["library_size"])
        self.assertEqual(1, len(effect.operations))
        self.assertIsInstance(effect.operations[0], DredgeDraw)
        self.assertEqual(3, effect.operations[0].mill_count)
        self.assertIn("object:A17@4", effect.effect_id)

    def test_dredge_descriptor_rejects_unknown_and_coerced_fields(self):
        handler = DredgeReplacementHandler()
        malformed = descriptor()
        malformed["unknown"] = True
        with self.assertRaisesRegex(SemanticNodeError, "unknown"):
            handler.validate(malformed)

        malformed = descriptor()
        malformed["modification"]["mill_count"] = "3"
        with self.assertRaisesRegex(SemanticNodeError, "positive integer"):
            handler.validate(malformed)

        malformed = descriptor()
        malformed["modification"]["mill_count"] = True
        with self.assertRaisesRegex(SemanticNodeError, "positive integer"):
            handler.validate(malformed)

    def test_draw_registry_is_frozen_and_capability_bound(self):
        registry = default_draw_replacement_registry()
        inventory = registry.inventory()

        self.assertEqual(3, len(inventory))
        self.assertEqual(
            {
                DREDGE_HANDLER_ID,
                DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
                DRAW_RESULT_MULTIPLIER_HANDLER_ID,
            },
            {value["handler_id"] for value in inventory},
        )
        self.assertEqual(
            {
                DREDGE_HANDLER_ID: ["zone.draw.library_to_hand"],
                DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID: [
                    "zone.draw.library_to_hand"
                ],
                DRAW_RESULT_MULTIPLIER_HANDLER_ID: [
                    "zone.draw.result_generated_ordering"
                ],
            },
            {
                value["handler_id"]: value["capability_dependencies"]
                for value in inventory
            },
        )
        with self.assertRaisesRegex(SemanticNodeError, "frozen"):
            registry.register(DredgeReplacementHandler())

    def test_source_context_rejects_unstable_identity(self):
        with self.assertRaisesRegex(SemanticNodeError, "nonempty string"):
            DrawReplacementSourceContext(
                source_ref="",
                source_object_id="object:A17",
                source_zone_change_counter=0,
                source_owner="A",
            )
        with self.assertRaisesRegex(SemanticNodeError, "nonnegative"):
            DrawReplacementSourceContext(
                source_ref="A17",
                source_object_id="object:A17",
                source_zone_change_counter=-1,
                source_owner="A",
            )


class DrawInstructionReplacementIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_two_draw_doublers_modify_instruction_before_individual_draws(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=121205,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        sources = [
            card
            for card in engine.state.cards.values()
            if card.printed_name == "Island"
        ][:2]
        self.assertEqual(2, len(sources))
        for source in sources:
            engine.move_card(
                source.object_id,
                "battlefield",
                controller="A",
                log=False,
                semantic_events=False,
            )
        engine.semantics.put(
            SemanticProgram(
                key="test:two-draw-doublers",
                label="Two draw doublers",
                oracle_id=sources[0].oracle_id,
                ability_id="test:two-draw-doublers",
                active_zone="battlefield",
                event="draw.instruction",
                handlers=[
                    {
                        "handler_id": DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
                        "schema_version": 1,
                        "event": "draw.instruction",
                        "condition": {
                            "affected_player_relation": "source_controller",
                        },
                        "modification": {"factor": 2},
                    }
                ],
                trust_level="provisional",
            )
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        hand_before = len(engine.state.players["A"].zones["hand"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence("A", 1, reason="doubled instruction")

        self.assertEqual(
            hand_before + 4,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(
            4,
            engine.state.players["A"].stats["cards_drawn_by_turn"][
                str(engine.state.turn_sequence)
            ],
        )


if __name__ == "__main__":
    unittest.main()
