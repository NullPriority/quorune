from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler.untap_step_templates import (
    static_untap_step_handler,
    static_untap_step_limit_handler,
)
from quorune.engine import TURN_STEPS
from quorune.object_query import ObjectQueryResult
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import (
    default_untap_step_component_registry,
    StaticUntapStepParticipationHandler,
    UNTAP_STEP_HANDLER_ID,
    UntapStepSourceContext,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.untap_step import plan_untap_step


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _permanent(
    name: str,
    text: str,
    *,
    suffix: int,
    type_line: str = "Enchantment",
    keywords: tuple[str, ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{2}{U}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def _row(
    object_id: str,
    *,
    controller: str,
    card_type: str = "creature",
) -> ObjectQueryResult:
    return ObjectQueryResult(
        object_id=object_id,
        ref=f"ref:{object_id}",
        printed_name=f"Fixture {object_id}",
        owner=controller,
        controller=controller,
        zone="battlefield",
        types=(card_type,),
    )


class UntapStepCompilerTests(unittest.TestCase):
    def test_closed_static_untap_templates_compile_exactly(self):
        cases = (
            (
                _permanent(
                    "Global Untap Fixture",
                    "Creatures don't untap during their controllers' "
                    "untap steps.",
                    suffix=502_300_001,
                ),
                "prohibition",
                "query",
                "subject_controller",
            ),
            (
                _permanent(
                    "Other Turn Fixture",
                    "Untap all permanents you control during each other "
                    "player's untap step.",
                    suffix=502_300_002,
                    type_line="Creature — Spirit",
                ),
                "additional",
                "query",
                "other_player",
            ),
            (
                _permanent(
                    "Self Untap Fixture",
                    "This creature doesn't untap during your untap step.",
                    suffix=502_300_003,
                    type_line="Creature — Construct",
                ),
                "prohibition",
                "source",
                "subject_controller",
            ),
        )
        capabilities = load_default_capability_registry()
        for record, instruction, subject, turn_relation in cases:
            with self.subTest(card=record.name):
                program = compile_card_program(
                    _NoRulingsDatabase(),
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                value = program.to_dict()
                self.assertEqual([], value["residuals"])
                handlers = [
                    handler
                    for ability in value["abilities"]
                    for handler in ability["runtime"]["handlers"]
                    if handler["handler_id"] == UNTAP_STEP_HANDLER_ID
                ]
                self.assertEqual(1, len(handlers))
                handler = handlers[0]
                self.assertEqual(instruction, handler["instruction"]["kind"])
                self.assertEqual(subject, handler["subject"]["relation"])
                self.assertEqual(
                    turn_relation,
                    handler["condition"]["turn_relation"],
                )
                ability = next(
                    ability
                    for ability in value["abilities"]
                    if handler in ability["runtime"]["handlers"]
                )
                span = ability["source_span"]
                self.assertEqual(1, span["line"])
                self.assertEqual(len(record.oracle_text), span["end"] - span["start"])
                self.assertIn(
                    "untap.step.static_participation",
                    ability["capability_dependencies"],
                )

        enchanted = static_untap_step_handler(
            "Enchanted creature doesn't untap during its controller's "
            "untap step.",
            source_name="Fixture Aura",
        )
        equipped = static_untap_step_handler(
            "Equipped creature doesn't untap during its controller's "
            "untap step.",
            source_name="Fixture Equipment",
        )
        self.assertEqual(
            "attached_object", enchanted[1]["subject"]["relation"]
        )
        self.assertEqual(
            "attached_object", equipped[1]["subject"]["relation"]
        )

    def test_unsupported_untap_step_wording_remains_residual(self):
        cases = (
            "You may untap all permanents you control during each other "
            "player's untap step.",
            "Untap up to two permanents you control during each other "
            "player's untap step.",
            "Creatures you control don't untap during your untap step.",
            "As long as this artifact is untapped, players can't untap more "
            "than two permanents during their untap steps.",
        )
        capabilities = load_default_capability_registry()
        for index, text in enumerate(cases, start=1):
            with self.subTest(text=text):
                program = compile_card_program(
                    _NoRulingsDatabase(),
                    _permanent(
                        f"Unsupported Untap Fixture {index}",
                        text,
                        suffix=502_301_000 + index,
                        type_line="Artifact",
                    ),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                residuals = program.to_dict()["residuals"]
                self.assertTrue(residuals)
                self.assertIn(text, {row["text"] for row in residuals})

        limit = static_untap_step_limit_handler(
            cases[-1], source_name="Unsupported Untap Fixture 4"
        )
        self.assertIsNotNone(limit)
        self.assertEqual("limit", limit[1]["instruction"]["kind"])
        self.assertEqual(2, limit[1]["instruction"]["maximum"])

    def test_untap_step_descriptor_rejects_malformed_shapes(self):
        compiled = static_untap_step_handler(
            "Creatures don't untap during their controllers' untap steps.",
            source_name="Fixture",
        )
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        handler = StaticUntapStepParticipationHandler()
        handler.validate(descriptor)

        unknown = copy.deepcopy(descriptor)
        unknown["unknown"] = True
        with self.assertRaisesRegex(SemanticNodeError, "unknown"):
            handler.validate(unknown)

        controlled = copy.deepcopy(descriptor)
        controlled["subject"]["predicate"]["controller"] = "A"
        with self.assertRaisesRegex(SemanticNodeError, "reserve"):
            handler.validate(controlled)

        limit = static_untap_step_limit_handler(
            "As long as this artifact is untapped, players can't untap "
            "more than two permanents during their untap steps.",
            source_name="Fixture",
        )
        self.assertIsNotNone(limit)
        boolean_maximum = copy.deepcopy(limit[1])
        boolean_maximum["instruction"]["maximum"] = True
        with self.assertRaisesRegex(SemanticNodeError, "nonnegative integer"):
            handler.validate(boolean_maximum)

        inventory = default_untap_step_component_registry().inventory()
        self.assertEqual(1, len(inventory))
        self.assertEqual(UNTAP_STEP_HANDLER_ID, inventory[0]["handler_id"])
        self.assertEqual(
            ["untap.step.static_participation"],
            inventory[0]["capability_dependencies"],
        )


class UntapStepParticipationModelTests(unittest.TestCase):
    def test_source_and_attached_prohibitions_use_current_relationships(self):
        handler = StaticUntapStepParticipationHandler()
        source_compiled = static_untap_step_handler(
            "Fixture Source doesn't untap during your untap step.",
            source_name="Fixture Source",
        )
        self.assertIsNotNone(source_compiled)
        source = handler.lower(
            source_compiled[1],
            UntapStepSourceContext(
                source_object_id="source",
                source_ref="S1",
                source_controller="A",
                source_tapped=True,
                component_id="source:0",
            ),
        )
        source_plan = plan_untap_step(
            "A", (_row("source", controller="A"),), source
        )
        self.assertEqual(("source",), source_plan.prohibited_object_ids)

        attached_compiled = static_untap_step_handler(
            "Enchanted creature doesn't untap during its controller's "
            "untap step.",
            source_name="Fixture Aura",
        )
        self.assertIsNotNone(attached_compiled)
        rows = (_row("first", controller="A"), _row("second", controller="A"))
        first = handler.lower(
            attached_compiled[1],
            UntapStepSourceContext(
                source_object_id="aura",
                source_ref="S2",
                source_controller="B",
                source_tapped=False,
                component_id="attached:0",
                attached_object_id="first",
            ),
        )
        second = handler.lower(
            attached_compiled[1],
            UntapStepSourceContext(
                source_object_id="aura",
                source_ref="S2",
                source_controller="B",
                source_tapped=False,
                component_id="attached:0",
                attached_object_id="second",
            ),
        )
        self.assertEqual(
            ("first",),
            plan_untap_step("A", rows, first).prohibited_object_ids,
        )
        self.assertEqual(
            ("second",),
            plan_untap_step("A", rows, second).prohibited_object_ids,
        )

    def test_untap_step_planner_and_compiler_mutants_are_killed(self):
        text = "Creatures don't untap during their controllers' untap steps."

        def assert_compiler_boundary() -> None:
            compiled = static_untap_step_handler(
                text, source_name="Mutation Fixture"
            )
            self.assertIsNotNone(compiled)
            self.assertEqual(
                ["creature"],
                compiled[1]["subject"]["predicate"]["types_all"],
            )

        assert_compiler_boundary()
        disabled_pattern = mock.Mock()
        disabled_pattern.fullmatch.return_value = None
        with mock.patch(
            "quorune.compiler.untap_step_templates._GLOBAL_PROHIBITION",
            disabled_pattern,
        ), mock.patch(
            "quorune.compiler.untap_step_templates._QUERY_PROHIBITION",
            disabled_pattern,
        ):
            with self.assertRaises(AssertionError):
                assert_compiler_boundary()

        compiled = static_untap_step_handler(
            text, source_name="Mutation Fixture"
        )
        handler = StaticUntapStepParticipationHandler()
        participation = handler.lower(
            compiled[1],
            UntapStepSourceContext(
                source_object_id="source",
                source_ref="S1",
                source_controller="B",
                source_tapped=False,
                component_id="mutation:0",
            ),
        )

        def assert_planner_boundary() -> None:
            plan = plan_untap_step(
                "A", (_row("creature", controller="A"),), participation
            )
            self.assertEqual(("creature",), plan.prohibited_object_ids)

        assert_planner_boundary()
        with mock.patch(
            "quorune.untap_step.object_matches_query", return_value=False
        ):
            with self.assertRaises(AssertionError):
                assert_planner_boundary()


class UntapStepParticipationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        self._clear_window(session)
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def _clear_window(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []

    @staticmethod
    def card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.is_card_object and card.printed_name == name
        )

    def enter_untap(self, session, active: str) -> None:
        self._clear_window(session)
        engine = session.engine
        engine.state.active_player = active
        engine.state.phase_index = TURN_STEPS.index(("beginning", "untap"))
        engine._enter_step()

    @staticmethod
    def battlefield(engine, card, controller: str) -> None:
        engine.move_card(
            card.object_id,
            "battlefield",
            controller=controller,
            log=False,
            semantic_events=False,
        )

    def test_global_and_other_player_participation_compose_in_four_players(self):
        session = self.session(502_302_001)
        engine = session.engine
        alarm = self.card(engine, "Intruder Alarm")
        muse = self.card(engine, "Seedborn Muse")
        active_creature = self.card(engine, "Arcum Dagsson")
        inactive_land = self.card(engine, "Island")
        for card, controller in (
            (alarm, "C"),
            (muse, "B"),
            (active_creature, "A"),
            (inactive_land, "B"),
        ):
            self.battlefield(engine, card, controller)
        active_creature.tapped = True
        muse.tapped = True
        inactive_land.tapped = True

        self.enter_untap(session, "A")

        self.assertTrue(active_creature.tapped)
        self.assertFalse(muse.tapped)
        self.assertFalse(inactive_land.tapped)

    def test_static_prohibition_does_not_consume_stun_and_next_step_marker_expires(
        self,
    ):
        session = self.session(502_302_002)
        engine = session.engine
        alarm = self.card(engine, "Intruder Alarm")
        stunned = self.card(engine, "Arcum Dagsson")
        delayed = self.card(engine, "Birds of Paradise")
        for card, controller in (
            (alarm, "C"),
            (stunned, "A"),
            (delayed, "A"),
        ):
            self.battlefield(engine, card, controller)
        stunned.tapped = True
        stunned.counters["stun"] = 1
        delayed.tapped = True
        delayed.annotations["does_not_untap_next"] = True

        self.enter_untap(session, "A")

        self.assertTrue(stunned.tapped)
        self.assertEqual(1, stunned.counters["stun"])
        self.assertTrue(delayed.tapped)
        self.assertNotIn("does_not_untap_next", delayed.annotations)

        engine.move_card(
            alarm.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        self.enter_untap(session, "A")
        self.assertTrue(stunned.tapped)
        self.assertNotIn("stun", stunned.counters)
        self.assertFalse(delayed.tapped)

        self.enter_untap(session, "A")
        self.assertFalse(stunned.tapped)

    def test_source_leaves_and_control_changes_recompute_participation(self):
        session = self.session(502_302_003)
        engine = session.engine
        muse = self.card(engine, "Seedborn Muse")
        b_land = self.card(engine, "Island")
        c_land = next(
            card
            for card in engine.state.cards.values()
            if card.is_card_object
            and card.printed_name == "Island"
            and card.object_id != b_land.object_id
        )
        for card, controller in (
            (muse, "B"),
            (b_land, "B"),
            (c_land, "C"),
        ):
            self.battlefield(engine, card, controller)
            card.tapped = True

        self.enter_untap(session, "A")
        self.assertFalse(b_land.tapped)
        self.assertTrue(c_land.tapped)

        b_land.tapped = True
        c_land.tapped = True
        muse.tapped = True
        engine.change_control(muse.object_id, "C", reason="fixture")
        self.enter_untap(session, "A")
        self.assertTrue(b_land.tapped)
        self.assertFalse(c_land.tapped)
        self.assertFalse(muse.tapped)

        b_land.tapped = True
        c_land.tapped = True
        engine.move_card(
            muse.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        self.enter_untap(session, "A")
        self.assertTrue(b_land.tapped)
        self.assertTrue(c_land.tapped)

    def test_uncompiled_oracle_text_cannot_change_untap(self):
        session = self.session(502_302_004)
        engine = session.engine
        source_ref = engine.create_token(
            "C",
            name="Uncompiled Untap Text",
            tapped=True,
            characteristics={
                "type_line": "Token Enchantment Creature — Construct",
                "oracle_text": (
                    "Creatures don't untap during their controllers' "
                    "untap steps."
                ),
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
        )[0]
        source = engine._resolve_object("C", source_ref, zones={"battlefield"})
        active = self.card(engine, "Arcum Dagsson")
        self.battlefield(engine, active, "A")
        active.tapped = True

        self.enter_untap(session, "A")

        self.assertFalse(active.tapped)
        self.assertTrue(source.tapped)
        self.assertIsNone(engine._semantic_pause_annotation())

    def test_typed_untap_participation_replays_exactly(self):
        session = self.session(502_302_005, players=2)
        engine = session.engine
        alarm = self.card(engine, "Intruder Alarm")
        muse = self.card(engine, "Seedborn Muse")
        active_creature = self.card(engine, "Arcum Dagsson")
        inactive_land = self.card(engine, "Island")
        for card, controller in (
            (alarm, "A"),
            (muse, "A"),
            (active_creature, "B"),
            (inactive_land, "A"),
        ):
            self.battlefield(engine, card, controller)
        active_creature.tapped = True
        muse.tapped = True
        inactive_land.tapped = True

        engine.state.active_player = "A"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine.state.phase = "ending"
        engine.state.step = "end_step"
        self._clear_window(session)
        engine._grant_priority("A")
        engine.pump()
        session.commands.clear()
        session.decisions.clear()
        session.initial_checkpoint = checkpoint_envelope(engine.state)

        for seat in ("A", "B"):
            result = session.act(
                f"pilot:{seat}",
                {"a": "pass", "reason": "Advance through the turn boundary."},
            )
            self.assertTrue(result.ok, result.summary)

        self.assertTrue(active_creature.tapped)
        self.assertFalse(muse.tapped)
        self.assertFalse(inactive_land.tapped)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-untap-participation"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(2, replay["commands"])


if __name__ == "__main__":
    unittest.main()
