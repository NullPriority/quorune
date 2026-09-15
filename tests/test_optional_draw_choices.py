from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.drawing import DrawPermission
from quorune.engine import CommanderEngine
from quorune.model import StackItem
from quorune.projection import StateProjector
from quorune.record import checkpoint_envelope, replay_record
from quorune.semantic_choices import (
    SemanticChoiceContext,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.optional_draw import OptionalDrawHandler
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import current_draw_permission


def query(*, permission_a: DrawPermission, permission_b: DrawPermission):
    return SnapshotSemanticChoiceQuery(
        seat_order=("A", "B"),
        active_order=("A", "B"),
        libraries_by_seat={"A": (), "B": ()},
        draw_permissions_by_seat={
            "A": permission_a.to_dict(),
            "B": permission_b.to_dict(),
        },
    )


def context(snapshot, *, actor: str = "A") -> SemanticChoiceContext:
    return SemanticChoiceContext(
        actor=actor,
        stack_ref="S1",
        stack_controller="A",
        stack_label="Optional draw witness",
        source_ref="A17",
        card_ref=None,
        semantic_program_id="test:optional-draw",
        semantic_program_version=1,
        query=snapshot,
    )


def continuation(effect) -> SemanticChoiceContinuation:
    return SemanticChoiceContinuation(
        handler_id="choice.draw.optional.v1",
        handler_version=1,
        stack_ref="S1",
        effect=effect,
        remaining=(),
        destination=None,
        note="Optional draw witness",
        semantic_frame=SemanticChoiceFrame(
            semantic_program_id="test:optional-draw",
            semantic_program_version=1,
            stack_object="S1",
            instruction_pointer=0,
            controller="A",
        ),
    )


class OptionalDrawChoiceTests(unittest.TestCase):
    def setUp(self):
        self.handler = OptionalDrawHandler()
        self.unlimited_a = DrawPermission("A", 0)
        self.unlimited_b = DrawPermission("B", 0)

    def test_empty_library_does_not_remove_optional_draw_choice(self):
        snapshot = query(
            permission_a=self.unlimited_a,
            permission_b=self.unlimited_b,
        )
        prepared = self.handler.prepare(
            {"op": "offer_draw", "player": "A", "count": 1},
            context(snapshot),
        )

        self.assertIsNotNone(prepared.request)
        self.assertEqual(
            ("draw", "decline"), prepared.request.choice.legal_values
        )

    def test_max_one_removes_optional_multi_draw_before_task_issue(self):
        limited = DrawPermission("A", 0, 1, ("limit:A",))
        prepared = self.handler.prepare(
            {"op": "offer_draw", "player": "A", "count": 2},
            context(query(permission_a=limited, permission_b=self.unlimited_b)),
        )

        self.assertIsNone(prepared.request)
        self.assertIsNotNone(prepared.auto_continue)

    def test_other_chooser_uses_prospective_drawer_legality(self):
        prohibited_b = DrawPermission("B", 0, 0, ("limit:B",))
        prepared = self.handler.prepare(
            {
                "op": "offer_draw",
                "player": "A",
                "drawer": "B",
                "count": 1,
            },
            context(
                query(
                    permission_a=self.unlimited_a,
                    permission_b=prohibited_b,
                )
            ),
        )

        self.assertIsNone(prepared.request)
        self.assertIsNotNone(prepared.auto_continue)

    def test_acceptance_prepends_canonical_mandatory_draw(self):
        snapshot = query(
            permission_a=self.unlimited_a,
            permission_b=self.unlimited_b,
        )
        effect = {
            "op": "offer_draw",
            "player": "A",
            "drawer": "B",
            "count": 1,
            "private": True,
        }
        completed = self.handler.complete(
            continuation(effect),
            {"choice": "draw"},
            snapshot,
        )

        self.assertEqual(
            {
                "op": "draw",
                "player": "B",
                "count": 1,
                "private": True,
            },
            dict(completed.prepend_effects[0]),
        )

    def test_completion_revalidates_and_rejects_coercion(self):
        prohibited = query(
            permission_a=DrawPermission("A", 0, 0, ("limit:A",)),
            permission_b=self.unlimited_b,
        )
        effect = {
            "op": "offer_draw",
            "player": "A",
            "drawer": "A",
            "count": 1,
            "private": True,
        }
        with self.assertRaisesRegex(SemanticChoiceError, "no longer legally"):
            self.handler.complete(
                continuation(effect), {"choice": "draw"}, prohibited
            )
        with self.assertRaisesRegex(SemanticChoiceError, "positive integer"):
            self.handler.prepare(
                {"op": "offer_draw", "player": "A", "count": True},
                context(
                    query(
                        permission_a=self.unlimited_a,
                        permission_b=self.unlimited_b,
                    )
                ),
            )
        with self.assertRaisesRegex(SemanticChoiceError, "fields are invalid"):
            self.handler.prepare(
                {
                    "op": "offer_draw",
                    "player": "A",
                    "unexpected": True,
                },
                context(
                    query(
                        permission_a=self.unlimited_a,
                        permission_b=self.unlimited_b,
                    )
                ),
            )


class OptionalDrawChoiceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_optional_draw_choice_is_seat_scoped_and_replays_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=121301,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        program = SemanticProgram(
            key="test:optional-draw-replay",
            label="Optional draw replay witness",
            effects=[
                {
                    "op": "offer_draw",
                    "player": "A",
                    "drawer": "A",
                    "count": 1,
                    "private": True,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="optional-draw-replay",
            ref="S-optional-draw-replay",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            program.effects,
            None,
            note="optional draw replay",
        )
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:A"))
        self.assertIsNone(projector._decision("pilot:B"))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        before = len(engine.state.players["A"].zones["hand"])

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "draw",
                "reason": "Take the legal optional draw.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "optional-draw-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_four_player_chooser_uses_prospective_drawer_legality(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=121303,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Island"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="B",
            log=False,
            semantic_events=False,
        )
        engine.semantics.put(
            SemanticProgram(
                key="test:prospective-drawer-restriction",
                label="Prospective drawer restriction",
                oracle_id=source.oracle_id,
                ability_id="test:prospective-drawer-restriction",
                active_zone="battlefield",
                event="draw.permission",
                handlers=[
                    {
                        "handler_id": "restriction.draw.maximum-per-turn.v1",
                        "schema_version": 1,
                        "event": "draw.permission",
                        "condition": {
                            "affected_player_relation": "source_controller",
                        },
                        "restriction": {"maximum_per_turn": 0},
                    }
                ],
                trust_level="provisional",
            )
        )
        choice = SemanticProgram(
            key="test:other-player-optional-draw",
            label="Other player optional draw",
            effects=[
                {
                    "op": "offer_draw",
                    "player": "A",
                    "drawer": "B",
                    "count": 1,
                    "private": True,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(choice)
        item = StackItem(
            stack_id="other-player-optional-draw",
            ref="S-other-player-optional-draw",
            kind="triggered_ability",
            controller="A",
            label=choice.label,
            semantic_key=choice.key,
            visibility=["A", "B", "C", "D"],
        )
        engine.state.stack.append(item)
        before = len(engine.state.players["B"].zones["hand"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.assertIsNone(
                current_draw_permission(engine, "A").maximum_per_turn
            )
            self.assertEqual(
                0,
                current_draw_permission(engine, "B").maximum_per_turn,
            )
            engine._begin_resolve_item(
                item,
                choice.effects,
                None,
                note="prospective drawer legality",
            )

        self.assertIsNone(engine.state.pending_decision)
        self.assertFalse(engine.state.stack)
        self.assertEqual(before, len(engine.state.players["B"].zones["hand"]))


if __name__ == "__main__":
    unittest.main()
