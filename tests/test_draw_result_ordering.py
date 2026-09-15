from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import keep_all, load_assets, make_session

from quorune.compiler.runtime_templates import (
    static_runtime_template,
)
from quorune.compiler.draw_templates import (
    fixed_draw_effect_template,
)
from quorune.cast_timing import type_line_has_card_type
from quorune.engine import CommanderEngine
from quorune.drawing import (
    DiscardDrawnCardUnlessType,
    DrawError,
    DrawEventRequest,
    DrawResume,
    QueuedDraw,
    RevealDrawnCard,
    prepare_draw_event,
    validate_prepared_draw,
)
from quorune.replacement import (
    CreateResultDraws,
    ReplacementClass,
    ReplacementEffect,
    operation_from_dict,
    operation_to_dict,
)
from quorune.replacement.operations import (
    ReplacementOperationError,
)
from quorune.semantic_runtime import (
    DRAW_RESULT_MULTIPLIER_HANDLER_ID,
    DrawReplacementSourceContext,
    DrawResultMultiplierHandler,
    default_semantic_interpreter,
)
from quorune.semantics import SemanticProgram
from quorune.model import StackItem
from quorune.oracle_ir import generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    load_default_capability_registry,
)


ORDER = ("A", "B", "C", "D")


def result_draw_effect(effect_id: str = "double:A11@2") -> ReplacementEffect:
    return ReplacementEffect(
        effect_id=effect_id,
        source_id="object:A11@2",
        event_kind="draw",
        replacement_class=ReplacementClass.OTHER,
        conditions={"affected_player": {"eq": "A"}, "is_draw": {"eq": True}},
        operations=(CreateResultDraws(count=2),),
    )


class DrawResultModelTests(unittest.TestCase):
    def test_result_draw_operation_is_closed_and_strict(self):
        operation = CreateResultDraws(count=2)
        self.assertEqual(
            {"op": "create_result_draws", "count": 2},
            operation_to_dict(operation),
        )
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))
        for count in (0, -1, True, "2"):
            with self.subTest(count=count):
                with self.assertRaises(ReplacementOperationError):
                    operation_from_dict(
                        {"op": "create_result_draws", "count": count}
                    )

    def test_result_draws_are_typed_and_exclude_the_producing_effect(self):
        effect = result_draw_effect()
        prepared = prepare_draw_event(
            DrawEventRequest(
                "draw:event:result",
                "A",
                8,
                reason="fixture draw",
                private=True,
            ),
            apnap_order=ORDER,
            effects=(effect,),
        )

        self.assertEqual("result_draws", prepared.resolution.kind)
        self.assertFalse(prepared.event.payload["is_draw"])
        self.assertEqual(
            (
                QueuedDraw(
                    "A",
                    2,
                    "fixture draw",
                    True,
                    (effect.effect_id,),
                ),
            ),
            prepared.resolution.result_draws,
        )
        self.assertEqual(
            (), prepared.resolution.result_draws[0].post_draw_actions
        )
        validate_prepared_draw(prepared, apnap_order=ORDER)

    def test_result_draws_preserve_ancestor_exclusions(self):
        effect = result_draw_effect("double:second")
        prepared = prepare_draw_event(
            DrawEventRequest(
                "draw:event:nested",
                "A",
                8,
                excluded_effect_ids=("double:first",),
            ),
            apnap_order=ORDER,
            effects=(effect,),
        )

        self.assertEqual(
            ("double:first", "double:second"),
            prepared.resolution.result_draws[0].excluded_effect_ids,
        )

    def test_result_draw_queue_serializes_its_final_resume_strictly(self):
        final = DrawResume(
            kind="turn_draw",
            seat="A",
        )
        value = DrawResume(
            kind="draw_batch",
            draws=(
                QueuedDraw(
                    "A",
                    2,
                    "replacement result",
                    True,
                    ("double:A11",),
                ),
            ),
            after=final,
        )
        self.assertEqual(value, DrawResume.from_dict(value.to_dict()))
        malformed = value.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(DrawError, "unknown"):
            DrawResume.from_dict(malformed)


class DrawResultComponentTests(unittest.TestCase):
    def test_draw_result_handler_binds_controller_and_typed_operation(self):
        descriptor = {
            "handler_id": DRAW_RESULT_MULTIPLIER_HANDLER_ID,
            "schema_version": 1,
            "event": "draw",
            "condition": {"affected_player_relation": "source_controller"},
            "modification": {"factor": 2},
        }
        effect = DrawResultMultiplierHandler().replacement_effect(
            descriptor,
            DrawReplacementSourceContext(
                source_ref="A11",
                source_object_id="object:A11",
                source_zone_change_counter=2,
                source_owner="A",
                source_controller="A",
                component_id="program:0",
            ),
        )

        self.assertEqual("draw", effect.event_kind)
        self.assertEqual({"eq": "A"}, effect.conditions["affected_player"])
        self.assertIsInstance(effect.operations[0], CreateResultDraws)

    def test_compiler_uses_result_draws_without_reinterpreting_legacy_records(self):
        template = static_runtime_template(
            "If you would draw a card, draw two cards instead."
        )

        self.assertIsNotNone(template)
        self.assertEqual("draw", template.event)
        self.assertEqual(
            DRAW_RESULT_MULTIPLIER_HANDLER_ID,
            template.compiled[1]["handler_id"],
        )

    def test_compiler_lowers_the_closed_specifically_drawn_card_family(self):
        template = fixed_draw_effect_template(
            "Draw a card and reveal it. If it isn't a land card, discard it."
        )

        self.assertIsNotNone(template)
        self.assertEqual(
            "draw-reveal-discard-unless-land-controller-v1",
            template[0],
        )
        effect = dict(template[1][0])
        effect["player"] = "A"
        plan = default_semantic_interpreter().lower_for_seats(
            effect,
            actor="A",
            default_reason="closed post-draw action",
            seats=("A", "B"),
            active_seats=("A", "B"),
            apnap_order=("A", "B"),
        )
        self.assertEqual(
            (
                RevealDrawnCard(),
                DiscardDrawnCardUnlessType(card_type="land"),
            ),
            plan.intents[0].post_draw_actions,
        )

    def test_compiler_leaves_unrepresented_draw_action_variants_residual(self):
        for text in (
            "Draw a card and reveal it. If it isn't a creature card, discard it.",
            "Draw a card. You may reveal it.",
            "Draw two cards and reveal them.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_draw_effect_template(text))


class DrawResultCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session_with_doublers(self, count: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=121701 + count,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        sources = [
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Island"
        ][:count]
        self.assertEqual(count, len(sources))
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
                key="test:draw-result-double",
                label="Draw result double",
                oracle_id=sources[0].oracle_id,
                ability_id="test:draw-result-double",
                active_zone="battlefield",
                event="draw",
                handlers=[
                    {
                        "handler_id": DRAW_RESULT_MULTIPLIER_HANDLER_ID,
                        "schema_version": 1,
                        "event": "draw",
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
        return session

    def session_without_replacements(self, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=121603,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        return session

    def install_real_result_doubler(self, engine):
        record = self.db.lookup("Thought Reflection")
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Island"
        )
        source.oracle_id = record.oracle_id
        source.printed_name = record.name
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        self.assertTrue(
            engine.semantic_program_is_current_trusted(programs[0])
        )
        return source

    @staticmethod
    def put_matching_card_on_top(engine, *, land: bool) -> str:
        library = engine.state.players["A"].zones["library"]
        for object_id in tuple(library):
            record = engine.card_record(engine.state.cards[object_id])
            if record is None:
                continue
            type_line = (
                str(record.faces[0].get("type_line") or "")
                if record.faces
                else record.type_line
            )
            if type_line_has_card_type(type_line, "land") == land:
                library.remove(object_id)
                library.append(object_id)
                return object_id
        raise AssertionError("Fixture library lacks the requested card type")

    @staticmethod
    def post_draw_actions():
        return (
            RevealDrawnCard(),
            DiscardDrawnCardUnlessType(card_type="land"),
        )

    def test_drawn_land_is_revealed_and_remains_in_hand(self):
        session = self.session_without_replacements(players=4)
        engine = session.engine
        object_id = self.put_matching_card_on_top(engine, land=True)
        event_before = engine.state.event_sequence

        engine._begin_draw_sequence(
            "A",
            1,
            reason="specifically drawn land",
            private=True,
            post_draw_actions=self.post_draw_actions(),
        )

        self.assertIn(object_id, engine.state.players["A"].zones["hand"])
        self.assertEqual(sorted(engine.seats), engine.state.cards[object_id].revealed_to)
        codes = [
            event.code
            for event in engine.state.events
            if event.event_id > event_before
        ]
        self.assertIn("card.draw.reveal", codes)
        self.assertNotIn("card.draw.discard", codes)
        for opponent in ("B", "C", "D"):
            opponent_view = session.packet(
                f"pilot:{opponent}", full=True
            )["state"]["players"]["A"]
            self.assertIn(
                engine.state.cards[object_id].ref,
                {card["id"] for card in opponent_view["known_hand"]},
            )

    def test_drawn_nonland_is_revealed_then_that_exact_card_is_discarded(self):
        session = self.session_without_replacements()
        engine = session.engine
        object_id = self.put_matching_card_on_top(engine, land=False)
        event_before = engine.state.event_sequence

        engine._begin_draw_sequence(
            "A",
            1,
            reason="specifically drawn nonland",
            private=True,
            post_draw_actions=self.post_draw_actions(),
        )

        self.assertNotIn(object_id, engine.state.players["A"].zones["hand"])
        self.assertIn(object_id, engine.state.players["A"].zones["graveyard"])
        codes = [
            event.code
            for event in engine.state.events
            if event.event_id > event_before
            and event.code in {"card.draw.reveal", "card.draw.discard"}
        ]
        self.assertEqual(
            ["card.draw.reveal", "card.draw.discard"], codes
        )

    def test_replacement_result_draws_do_not_inherit_original_post_actions(self):
        session = self.session_with_doublers(1)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])
        event_before = engine.state.event_sequence

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence(
                "A",
                1,
                reason="replaced specifically drawn card",
                private=True,
                post_draw_actions=self.post_draw_actions(),
            )

        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )
        opponent = session.packet("pilot:B", full=True)["state"][
            "players"
        ]["A"]
        self.assertNotIn("hand", opponent)
        self.assertFalse(opponent.get("known_hand", []))
        self.assertFalse(
            any(
                event.event_id > event_before
                and event.code
                in {"card.draw.reveal", "card.draw.discard"}
                for event in engine.state.events
            )
        )

    @staticmethod
    def put_draw_program_on_stack(engine, program: SemanticProgram) -> None:
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id=f"stack:{program.key}",
                ref=f"S:{program.key}",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=list(engine.seats),
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")

    def test_specifically_drawn_card_actions_replay_exactly(self):
        session = self.session_without_replacements()
        engine = session.engine
        object_id = self.put_matching_card_on_top(engine, land=False)
        self.put_draw_program_on_stack(
            engine,
            SemanticProgram(
                key="test:draw-with-actions",
                label="Draw with specifically drawn card actions",
                effects=[
                    {
                        "op": "draw_with_actions",
                        "player": "$controller",
                        "count": 1,
                        "private": True,
                        "post_draw_actions": [
                            {"action": "reveal", "public": True},
                            {
                                "action": "discard_unless_type",
                                "card_type": "land",
                            },
                        ],
                    }
                ],
                trust_level="provisional",
            ),
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertIn(object_id, engine.state.players["A"].zones["graveyard"])
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "draw-with-actions-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_malformed_post_draw_action_rolls_back_before_draw_mutation(self):
        session = self.session_without_replacements()
        engine = session.engine
        top = engine.state.players["A"].zones["library"][-1]
        self.put_draw_program_on_stack(
            engine,
            SemanticProgram(
                key="test:invalid-draw-with-actions",
                label="Invalid draw with actions",
                effects=[
                    {
                        "op": "draw_with_actions",
                        "player": "$controller",
                        "count": 1,
                        "private": True,
                        "post_draw_actions": [
                            {"action": "reveal", "public": True},
                            {
                                "action": "discard_unless_type",
                                "card_type": "creature",
                            },
                        ],
                    }
                ],
                trust_level="provisional",
            ),
        )

        first = session.act("pilot:A", {"action_id": "pass"})
        self.assertTrue(first.ok, first.summary)
        before = authoritative_state_hash(engine.state)
        second = session.act("pilot:B", {"action_id": "pass"})

        self.assertFalse(second.ok)
        self.assertIn("requires land", second.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("library", engine.state.cards[top].zone)

    def test_result_generated_draw_order_replays_exactly(self):
        session = self.session_without_replacements()
        engine = session.engine
        self.install_real_result_doubler(engine)
        self.put_draw_program_on_stack(
            engine,
            SemanticProgram(
                key="test:draw-result-ordering",
                label="Draw replaced by result draws",
                effects=[
                    {
                        "op": "draw",
                        "player": "$controller",
                        "count": 1,
                        "private": True,
                    }
                ],
                trust_level="provisional",
            ),
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        hand_before = len(engine.state.players["A"].zones["hand"])

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "draw-result-order-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_result_draws_preserve_four_player_apnap_instruction_order(self):
        session = self.session_without_replacements(players=4)
        engine = session.engine
        self.install_real_result_doubler(engine)
        before = {
            seat: len(player.zones["hand"])
            for seat, player in engine.state.players.items()
        }

        engine.apply_effect(
            {"op": "draw_each_player", "count": 1}, actor="A"
        )

        self.assertEqual(
            {"A": 2, "B": 1, "C": 1, "D": 1},
            {
                seat: len(player.zones["hand"]) - before[seat]
                for seat, player in engine.state.players.items()
            },
        )

    def test_single_result_doubler_finishes_without_recursing(self):
        session = self.session_with_doublers(1)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence(
                "A",
                1,
                reason="single result replacement",
                private=True,
            )

        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertIsNone(engine.state.pending_decision)

    def test_result_draws_finish_before_the_original_sequence_resumes(self):
        session = self.session_with_doublers(1)
        engine = session.engine
        event_before = engine.state.event_sequence

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence(
                "A",
                2,
                reason="ordered result replacement",
                private=True,
            )

        relevant = [
            event.code
            for event in engine.state.events
            if event.event_id > event_before
            and event.code
            in {"card.draw.replaced.result_draws", "card.draw"}
        ]
        self.assertEqual(
            [
                "card.draw.replaced.result_draws",
                "card.draw",
                "card.draw",
                "card.draw.replaced.result_draws",
                "card.draw",
                "card.draw",
            ],
            relevant,
        )

    def test_two_result_doublers_require_one_order_choice_then_draw_four(self):
        session = self.session_with_doublers(2)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            engine._begin_draw_sequence(
                "A",
                1,
                reason="two result replacements",
                private=True,
            )
            packet = session.packet("pilot:A", full=True)
            self.assertEqual("draw.replacement", packet["decision"]["kind"])
            choice = packet["decision"]["ctx"]["options"][0]["id"]
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choice": choice,
                    "reason": "Choose one applicable draw replacement.",
                },
            )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            hand_before + 4,
            len(engine.state.players["A"].zones["hand"]),
        )


if __name__ == "__main__":
    unittest.main()
