from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session

from quorune.drawing import (
    DrawError,
    DrawEventRequest,
    DrawPermission,
    DrawRestriction,
    evaluate_draw_permission,
    prepare_draw_event,
    require_payable_draw_cost,
    validate_prepared_draw,
)
from quorune.engine import CommanderEngine
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import (
    DRAW_MAXIMUM_HANDLER_ID,
    DrawMaximumHandler,
    DrawRestrictionSourceContext,
    default_draw_restriction_registry,
    current_draw_permission,
)
from quorune.semantic_runtime.context import SemanticNodeError


ORDER = ("A", "B", "C", "D")


def descriptor(
    *, relation: str = "opponent", maximum: int = 1
) -> dict[str, object]:
    return {
        "handler_id": DRAW_MAXIMUM_HANDLER_ID,
        "schema_version": 1,
        "event": "draw.permission",
        "condition": {"affected_player_relation": relation},
        "restriction": {"maximum_per_turn": maximum},
    }


class DrawRestrictionModelTests(unittest.TestCase):
    def test_permission_uses_strictest_current_maximum(self):
        permission = evaluate_draw_permission(
            "B",
            drawn_this_turn=1,
            restrictions=(
                DrawRestriction("limit:z", "C17", 1),
                DrawRestriction("limit:a", "A17", 0),
            ),
        )

        self.assertEqual(0, permission.maximum_per_turn)
        self.assertEqual(("limit:a", "limit:z"), permission.restriction_ids)
        self.assertFalse(permission.allows_individual_draw())
        self.assertFalse(permission.allows_complete_draw(1))
        self.assertEqual(permission, DrawPermission.from_dict(permission.to_dict()))

    def test_optional_empty_library_draw_remains_legal_without_a_prohibition(self):
        permission = evaluate_draw_permission(
            "A", drawn_this_turn=0
        )

        self.assertTrue(permission.allows_individual_draw())
        self.assertTrue(permission.allows_complete_draw(1))
        self.assertTrue(permission.allows_complete_draw(7))

    def test_max_one_allows_partial_mandatory_draw_but_not_choice_or_cost(self):
        permission = evaluate_draw_permission(
            "A",
            drawn_this_turn=0,
            restrictions=(DrawRestriction("limit:one", "A21", 1),),
        )

        self.assertTrue(permission.allows_individual_draw())
        self.assertFalse(permission.allows_complete_draw(2))
        with self.assertRaisesRegex(DrawError, "cannot pay a cost"):
            require_payable_draw_cost(permission, 2)

    def test_permission_rejects_coercion_and_noncanonical_ids(self):
        with self.assertRaisesRegex(DrawError, "nonnegative integer"):
            DrawPermission("A", True)
        with self.assertRaisesRegex(DrawError, "unique and canonical"):
            DrawPermission("A", 0, 1, ("z", "a"))
        value = DrawPermission("A", 0).to_dict()
        value["unknown"] = True
        with self.assertRaisesRegex(DrawError, "fields are invalid"):
            DrawPermission.from_dict(value)

    def test_prohibited_draw_is_canonical_and_never_enters_replacement_ordering(self):
        request = DrawEventRequest("draw:prohibited", "A", 0)
        prepared = prepare_draw_event(
            request,
            apnap_order=ORDER,
            prohibition_ids=("limit:A17",),
        )

        self.assertEqual("prohibited", prepared.resolution.kind)
        self.assertFalse(prepared.event.payload["is_draw"])
        self.assertEqual(("limit:A17",), prepared.resolution.prohibition_ids)
        validate_prepared_draw(prepared, apnap_order=ORDER)

        tampered = replace(prepared, prohibition_ids=("limit:B17",))
        with self.assertRaisesRegex(DrawError, "state changed"):
            validate_prepared_draw(tampered, apnap_order=ORDER)

    def test_prohibition_rejects_replacement_state_and_bad_ids(self):
        request = DrawEventRequest("draw:prohibited:bad", "A", 3)
        with self.assertRaisesRegex(DrawError, "unique, and canonical"):
            prepare_draw_event(
                request,
                apnap_order=ORDER,
                prohibition_ids=("z", "a"),
            )
        with self.assertRaisesRegex(DrawError, "cannot enter replacement"):
            prepare_draw_event(
                request,
                apnap_order=ORDER,
                effects=(object(),),
                prohibition_ids=("limit:A",),
            )


class DrawRestrictionComponentTests(unittest.TestCase):
    def context(
        self, player: str, *, controller: str = "A"
    ) -> DrawRestrictionSourceContext:
        return DrawRestrictionSourceContext(
            source_ref="A17",
            source_controller=controller,
            prospective_player=player,
            component_id="program:0",
        )

    def test_opponent_and_controller_relations_lower_exactly(self):
        handler = DrawMaximumHandler()
        self.assertEqual((), handler.lower(descriptor(), self.context("A")))
        restriction = handler.lower(descriptor(), self.context("B"))[0]
        self.assertEqual(1, restriction.maximum_per_turn)
        self.assertIn("A17", restriction.restriction_id)

        controller = handler.lower(
            descriptor(relation="source_controller", maximum=0),
            self.context("A"),
        )[0]
        self.assertEqual(0, controller.maximum_per_turn)

    def test_descriptor_rejects_unknown_and_coerced_values(self):
        handler = DrawMaximumHandler()
        malformed = descriptor()
        malformed["unknown"] = True
        with self.assertRaisesRegex(SemanticNodeError, "unknown"):
            handler.validate(malformed)

        malformed = descriptor(maximum=1)
        malformed["restriction"]["maximum_per_turn"] = True
        with self.assertRaisesRegex(SemanticNodeError, "integer 0 or 1"):
            handler.validate(malformed)

        malformed = descriptor(relation="each")
        with self.assertRaisesRegex(SemanticNodeError, "any, opponent"):
            handler.validate(malformed)

    def test_registry_is_frozen_and_capability_bound(self):
        registry = default_draw_restriction_registry()
        inventory = registry.inventory()
        self.assertEqual(1, len(inventory))
        self.assertEqual(DRAW_MAXIMUM_HANDLER_ID, inventory[0]["handler_id"])
        self.assertEqual(
            ["zone.draw.library_to_hand"],
            inventory[0]["capability_dependencies"],
        )
        with self.assertRaisesRegex(SemanticNodeError, "frozen"):
            registry.register(DrawMaximumHandler())


class DrawRestrictionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        return session

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def stage_restriction(
        self,
        engine,
        *,
        controller: str,
        relation: str,
        maximum: int,
    ):
        source = self.card(engine, controller, "Island")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller=controller,
            log=False,
            semantic_events=False,
        )
        engine.semantics.put(
            SemanticProgram(
                key=f"test:draw-restriction:{source.object_id}",
                label="Draw restriction fixture",
                oracle_id=source.oracle_id,
                ability_id="test:draw-restriction-fixture",
                active_zone="battlefield",
                event="draw.permission",
                handlers=[
                    descriptor(relation=relation, maximum=maximum),
                ],
                trust_level="provisional",
            )
        )
        return source

    def test_mandatory_multi_draw_partially_occurs_under_maximum_one(self):
        session = self.session(121201)
        engine = session.engine
        self.stage_restriction(
            engine,
            controller="A",
            relation="source_controller",
            maximum=1,
        )
        before = len(engine.state.players["A"].zones["hand"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence("A", 2, reason="mandatory draw two")

        self.assertEqual(before + 1, len(engine.state.players["A"].zones["hand"]))
        self.assertEqual(
            1,
            engine.state.players["A"].stats["cards_drawn_by_turn"][
                str(engine.state.turn_sequence)
            ],
        )
        self.assertEqual(
            1,
            sum(
                event.code == "card.draw.prohibited"
                for event in engine.state.events
            ),
        )

    def test_draw_prohibition_is_checked_before_dredge_replacements(self):
        session = self.session(121202)
        engine = session.engine
        self.stage_restriction(
            engine,
            controller="A",
            relation="any",
            maximum=0,
        )
        loam = self.card(engine, "B", "Life from the Loam")
        engine.move_card(
            loam.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        hand_before = len(engine.state.players["B"].zones["hand"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence("B", 1, reason="prohibited draw")

        self.assertIsNone(engine.state.pending_decision)
        self.assertEqual("graveyard", loam.zone)
        self.assertEqual(hand_before, len(engine.state.players["B"].zones["hand"]))
        self.assertEqual("card.draw.prohibited", engine.state.events[-1].code)

    def test_dredge_does_not_consume_maximum_one_draw_allowance(self):
        session = self.session(121203)
        engine = session.engine
        self.stage_restriction(
            engine,
            controller="B",
            relation="source_controller",
            maximum=1,
        )
        loam = self.card(engine, "B", "Life from the Loam")
        engine.move_card(
            loam.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        library_before = len(engine.state.players["B"].zones["library"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence("B", 2, reason="dredge then draw")
            result = session.act(
                "pilot:B",
                {
                    "action_id": "choose",
                    "choice": loam.ref,
                    "reason": "Replace only the first draw.",
                },
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", loam.zone)
        self.assertEqual(
            library_before - 4,
            len(engine.state.players["B"].zones["library"]),
        )
        self.assertEqual(
            1,
            engine.state.players["B"].stats["cards_drawn_by_turn"][
                str(engine.state.turn_sequence)
            ],
        )
        self.assertFalse(
            any(event.code == "card.draw.prohibited" for event in engine.state.events)
        )

    def test_restriction_rechecks_control_phasing_and_source_zone(self):
        session = self.session(121204)
        engine = session.engine
        source = self.stage_restriction(
            engine,
            controller="A",
            relation="opponent",
            maximum=1,
        )

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.assertIsNone(current_draw_permission(engine, "A").maximum_per_turn)
            self.assertEqual(1, current_draw_permission(engine, "B").maximum_per_turn)
            source.controller = "B"
            self.assertEqual(1, current_draw_permission(engine, "A").maximum_per_turn)
            self.assertIsNone(current_draw_permission(engine, "B").maximum_per_turn)
            source.phased_out = True
            self.assertIsNone(current_draw_permission(engine, "A").maximum_per_turn)
            source.phased_out = False
            engine.move_card(
                source.object_id,
                "graveyard",
                log=False,
                semantic_events=False,
            )
            self.assertIsNone(current_draw_permission(engine, "A").maximum_per_turn)


if __name__ == "__main__":
    unittest.main()
