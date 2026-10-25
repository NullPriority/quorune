from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.engine import GameRuleError
from quorune.model import StackItem
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantic_runtime import (
    BecomeMonarchIntent,
    DrawCardsIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    SetPermanentTappedIntent,
    SemanticHandlerRegistry,
    SemanticHandlerRegistryError,
    UntapAllCreaturesIntent,
    default_semantic_handler_registry,
)
from quorune.semantic_runtime.generic import (
    BecomeMonarchHandler,
    DrawEachPlayerHandler,
    DrawHandler,
)
from quorune.semantic_choices import (
    SemanticChoiceContext,
    SemanticChoiceError,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.conditional_draw import (
    OpponentCastColorDrawHandler,
)
from quorune.semantic_runtime.tap_state_handlers import (
    TapPermanentHandler,
    UntapAllCreaturesHandler,
    UntapPermanentHandler,
)
from quorune.semantics import SemanticProgram, _is_supported_effect_operation


class TypedSemanticHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 3):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def context(*, actor: str = "A") -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor=actor,
            default_reason="test effect",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C"),
                active_seats=("A", "B", "C"),
                apnap_order=("B", "C", "A"),
            ),
        )

    def test_registry_is_deterministic_and_rejects_duplicate_ownership(self):
        first = default_semantic_handler_registry()
        second = SemanticHandlerRegistry(reversed((
            DrawHandler(),
            BecomeMonarchHandler(),
        )))
        reordered = SemanticHandlerRegistry((
            BecomeMonarchHandler(),
            DrawHandler(),
        ))
        self.assertEqual(second.inventory(), reordered.inventory())
        self.assertEqual(second.fingerprint, reordered.fingerprint)
        inventory = first.inventory()
        self.assertEqual(
            len(inventory),
            len({row["operation"] for row in inventory}),
        )
        effect_operations = {
            row["operation"]
            for row in inventory
            if str(row["family"]).startswith("effect.")
        }
        self.assertTrue(effect_operations)
        self.assertTrue(all(_is_supported_effect_operation(operation) for operation in effect_operations))
        self.assertFalse(_is_supported_effect_operation('unregistered_operation_fixture'))
        self.assertIn("place_counter_batch", effect_operations)
        with self.assertRaisesRegex(
            SemanticHandlerRegistryError, "Duplicate semantic operation"
        ):
            SemanticHandlerRegistry((DrawHandler(), DrawHandler()))
        with self.assertRaisesRegex(
            SemanticHandlerRegistryError, "registry is frozen"
        ):
            first.register(DrawHandler())

    def test_contract_traces_tap_and_untap_rules(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (root / "mechanics" / "contracts" / "tap-and-untap.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {"122.1d", "202.3", "701.26", "701.26a", "701.26b"},
            set(contract["rule_references"]),
        )

    def test_draw_handler_lowers_typed_intent_through_read_only_context(self):
        context = self.context()
        plan = DrawHandler().lower(
            {"op": "draw", "player": "B", "count": 2},
            context,
        )
        self.assertEqual("generic.draw.v1", plan.handler_id)
        self.assertEqual(
            (
                DrawCardsIntent(
                    player="B",
                    count=2,
                    reason="test effect",
                ),
            ),
            plan.intents,
        )
        self.assertFalse(hasattr(context, "state"))
        with self.assertRaises(FrozenInstanceError):
            context.actor = "C"  # type: ignore[misc]
        private_plan = DrawHandler().lower(
            {"op": "draw", "player": "B", "private": True},
            context,
        )
        self.assertTrue(private_plan.intents[0].private)

    def test_conditional_opponent_color_draw_is_strict_and_public_fact_based(self):
        handler = OpponentCastColorDrawHandler()
        query = SnapshotSemanticChoiceQuery(
            seat_order=("A", "B"),
            active_order=("A", "B"),
            opponent_cast_colors_by_seat={"B": ("U",)},
        )
        context = SemanticChoiceContext(
            actor="B",
            stack_ref="S-conditional-draw",
            stack_controller="B",
            stack_label="Conditional draw test",
            source_ref=None,
            card_ref=None,
            semantic_program_id="test:conditional-draw",
            semantic_program_version=1,
            query=query,
        )

        matched = handler.prepare(
            {
                "op": handler.operation,
                "player": "B",
                "colors": ["U", "B"],
            },
            context,
        )
        self.assertEqual(1, len(matched.preparation_intents))
        self.assertIsInstance(matched.preparation_intents[0], DrawCardsIntent)

        unmatched = handler.prepare(
            {
                "op": handler.operation,
                "player": "B",
                "colors": ["R", "G"],
            },
            context,
        )
        self.assertEqual((), unmatched.preparation_intents)

        with self.assertRaisesRegex(SemanticChoiceError, "unique Magic colors"):
            handler.prepare(
                {
                    "op": handler.operation,
                    "player": "B",
                    "colors": ["U", "U"],
                },
                context,
            )

    def test_draw_each_player_uses_apnap_order_and_exact_engine_path(self):
        session = self.session(1210401)
        engine = session.engine
        engine.state.active_player = "B"
        before = {
            seat: len(engine.state.players[seat].zones["hand"])
            for seat in engine.active_seats
        }

        result = engine.apply_effect(
            {"op": "draw_each_player", "count": 1},
            actor="A",
        )

        self.assertEqual(["B", "C", "A"], list(result))
        self.assertTrue(all(len(cards) == 1 for cards in result.values()))
        for seat in engine.active_seats:
            self.assertEqual(
                before[seat] + 1,
                len(engine.state.players[seat].zones["hand"]),
            )

    def test_draw_each_player_pauses_and_resumes_in_apnap_order_for_dredge(self):
        session = self.session(12104011)
        engine = session.engine
        engine.state.active_player = "B"
        loam = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Life from the Loam"
        )
        engine.move_card(
            loam.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        hand_before = {
            seat: len(engine.state.players[seat].zones["hand"])
            for seat in engine.active_seats
        }

        engine.apply_effect(
            {"op": "draw_each_player", "count": 1},
            actor="A",
        )

        self.assertEqual("draw.replacement", engine.state.pending_decision.kind)
        self.assertEqual(["B"], engine.state.pending_decision.actors)
        self.assertTrue(
            all(
                len(engine.state.players[seat].zones["hand"])
                == hand_before[seat]
                for seat in engine.active_seats
            )
        )

        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": loam.ref,
                "reason": "Use the available Dredge replacement.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", loam.zone)
        self.assertEqual(
            hand_before["C"] + 1,
            len(engine.state.players["C"].zones["hand"]),
        )
        self.assertEqual(
            hand_before["A"] + 1,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_monarch_handler_uses_canonical_engine_mutation_path(self):
        session = self.session(7250401)
        engine = session.engine
        result = engine.apply_effect(
            {"op": "become_monarch", "player": "B"},
            actor="A",
        )
        self.assertEqual("B", result)
        self.assertEqual("B", engine.state.monarch)
        self.assertIsInstance(
            BecomeMonarchHandler().lower(
                {"op": "become_monarch", "player": "C"},
                self.context(),
            ).intents[0],
            BecomeMonarchIntent,
        )
        self.assertEqual(
            "monarch.change",
            [event for event in engine.state.events if event.code == "monarch.change"][-1].code,
        )

    def test_monarch_designation_mutant_is_killed(self):
        control = self.session(7250402)
        mutant = self.session(7250403)

        control.engine.apply_effect(
            {"op": "become_monarch", "player": "B"},
            actor="A",
        )
        with patch(
            "quorune.engine.CommanderEngine.become_monarch",
            return_value=None,
        ):
            mutant.engine.apply_effect(
                {"op": "become_monarch", "player": "B"},
                actor="A",
            )

        self.assertEqual("B", control.state.monarch)
        self.assertIsNone(mutant.state.monarch)
        self.assertNotEqual(
            authoritative_state_hash(control.state),
            authoritative_state_hash(mutant.state),
        )

    def test_tap_state_handlers_lower_typed_intents_through_read_only_context(self):
        context = self.context(actor="B")
        tap = TapPermanentHandler().lower(
            {"op": "tap", "card": "C9"}, context
        )
        untap = UntapPermanentHandler().lower(
            {"op": "untap", "card": "C9", "reason": "ready it"},
            context,
        )
        all_creatures = UntapAllCreaturesHandler().lower(
            {"op": "untap_all_creatures"}, context
        )

        self.assertEqual("generic.tap-permanent.v2", tap.handler_id)
        self.assertEqual(
            SetPermanentTappedIntent(
                object_ref="C9",
                actor="B",
                tapped=True,
                reason="test effect",
            ),
            tap.intents[0],
        )
        self.assertEqual(
            SetPermanentTappedIntent(
                object_ref="C9",
                actor="B",
                tapped=False,
                reason="ready it",
            ),
            untap.intents[0],
        )
        self.assertEqual(
            UntapAllCreaturesIntent(actor="B", reason="test effect"),
            all_creatures.intents[0],
        )
        self.assertFalse(hasattr(context, "state"))

    @staticmethod
    def permanent(
        engine,
        seat: str,
        name: str,
        *,
        type_line: str = "Token Creature — Test",
        tapped: bool = False,
    ):
        ref = engine.create_token(
            seat,
            name=name,
            tapped=tapped,
            characteristics={
                "type_line": type_line,
                "power": "1" if "Creature" in type_line else None,
                "toughness": "1" if "Creature" in type_line else None,
            },
            reason="tap-state fixture",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    def test_tap_state_effects_change_only_eligible_state(self):
        session = self.session(7012601)
        engine = session.engine
        card = self.permanent(engine, "A", "Tap-state Witness")

        self.assertEqual(
            card.ref,
            engine.apply_effect(
                {"op": "tap", "card": card.ref}, actor="A"
            ),
        )
        self.assertTrue(card.tapped)
        engine.apply_effect({"op": "tap", "card": card.ref}, actor="A")
        engine.apply_effect({"op": "untap", "card": card.ref}, actor="A")
        self.assertFalse(card.tapped)
        engine.apply_effect({"op": "untap", "card": card.ref}, actor="A")

        self.assertEqual(
            1,
            sum(event.code == "permanent.tap" for event in engine.state.events),
        )
        self.assertEqual(
            1,
            sum(
                event.code == "permanent.untap"
                for event in engine.state.events
            ),
        )

    def test_stun_counter_replaces_typed_untap_without_false_event(self):
        session = self.session(1220104)
        engine = session.engine
        card = self.permanent(
            engine, "A", "Stunned Witness", tapped=True
        )
        card.counters["stun"] = 1
        event_count = len(engine.state.events)

        engine.apply_effect(
            {"op": "untap", "card": card.ref}, actor="A"
        )

        self.assertTrue(card.tapped)
        self.assertNotIn("stun", card.counters)
        new_events = engine.state.events[event_count:]
        self.assertEqual(
            ["permanent.untap.replaced"],
            [event.code for event in new_events],
        )

        engine.apply_effect(
            {"op": "untap", "card": card.ref}, actor="A"
        )
        self.assertFalse(card.tapped)
        self.assertEqual("permanent.untap", engine.state.events[-1].code)

    def test_untap_all_creatures_uses_effective_types_and_canonical_state_path(self):
        session = self.session(7012602)
        engine = session.engine
        ordinary = self.permanent(
            engine, "A", "Ordinary Creature", tapped=True
        )
        animated = self.permanent(
            engine,
            "B",
            "Animated Artifact",
            type_line="Token Artifact",
            tapped=True,
        )
        animated.annotations["until_end_of_turn"] = {
            "add_types": ["Creature"]
        }
        stunned = self.permanent(
            engine, "C", "Stunned Creature", tapped=True
        )
        stunned.counters["stun"] = 1
        phased = self.permanent(
            engine, "B", "Phased Creature", tapped=True
        )
        phased.phased_out = True
        artifact = self.permanent(
            engine,
            "C",
            "Plain Artifact",
            type_line="Token Artifact",
            tapped=True,
        )

        result = engine.apply_effect(
            {"op": "untap_all_creatures"}, actor="A"
        )

        self.assertEqual([ordinary.ref, animated.ref], result)
        self.assertFalse(ordinary.tapped)
        self.assertFalse(animated.tapped)
        self.assertTrue(stunned.tapped)
        self.assertNotIn("stun", stunned.counters)
        self.assertTrue(phased.tapped)
        self.assertTrue(artifact.tapped)
        self.assertEqual(
            [ordinary.ref, animated.ref],
            engine.state.events[-1].details["objects"],
        )

    def test_tap_state_validation_fails_before_mutation(self):
        session = self.session(7012603)
        engine = session.engine
        card = self.permanent(engine, "A", "Validation Witness")
        before = authoritative_state_hash(engine.state)
        invalid = (
            {"op": "tap", "card": ""},
            {"op": "untap", "card": 7},
            {"op": "untap_all_creatures", "card": card.ref},
        )
        for effect in invalid:
            with self.assertRaisesRegex(
                GameRuleError,
                "one nonempty target reference|unknown fields",
            ):
                engine.apply_effect(effect, actor="A")
            self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_registered_node_validation_fails_before_mutation(self):
        session = self.session(1210402)
        engine = session.engine
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(
            GameRuleError, "Draw count must be a nonnegative integer"
        ):
            engine.apply_effect(
                {"op": "draw", "count": "2"},
                actor="A",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_unmigrated_operation_retains_legacy_dispatch(self):
        session = self.session(1190401)
        engine = session.engine
        before = engine.state.players["A"].life
        result = engine.apply_effect(
            {"op": "life", "player": "A", "delta": 2},
            actor="A",
        )
        self.assertEqual(before + 2, result)
        self.assertEqual(before + 2, engine.state.players["A"].life)

    def test_migrated_semantic_effect_replays_exactly(self):
        session = self.session(1210403, players=2)
        engine = session.engine
        program = SemanticProgram(
            key="test:typed-draw",
            label="Typed draw",
            effects=[
                {
                    "op": "draw",
                    "player": "$controller",
                    "count": 1,
                    "private": True,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="typed-draw",
                ref="S-typed-draw",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        hand_before = len(engine.state.players["A"].zones["hand"])

        original_lower = DrawHandler.lower
        with patch.object(
            DrawHandler,
            "lower",
            autospec=True,
            side_effect=original_lower,
        ) as lower:
            for principal in ("pilot:A", "pilot:B"):
                result = session.act(principal, {"action_id": "pass"})
                self.assertTrue(result.ok, result.summary)
        self.assertGreaterEqual(lower.call_count, 1)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        private_draw = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.draw.private" and event.actor == "A"
        )
        self.assertEqual(0, private_draw.importance)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-draw-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_stack_draw_node_validation_rolls_back_before_resolution(self):
        session = self.session(1210404, players=2)
        engine = session.engine
        program = SemanticProgram(
            key="test:invalid-typed-draw",
            label="Invalid typed draw",
            effects=[
                {"op": "draw", "player": "$controller", "count": "1"}
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="invalid-typed-draw",
                ref="S-invalid-typed-draw",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")

        first = session.act("pilot:A", {"action_id": "pass"})
        self.assertTrue(first.ok, first.summary)
        before = authoritative_state_hash(engine.state)
        second = session.act("pilot:B", {"action_id": "pass"})

        self.assertFalse(second.ok)
        self.assertIn(
            "Draw count must be a nonnegative integer", second.summary
        )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(
            ["S-invalid-typed-draw"],
            [item.ref for item in engine.state.stack],
        )

    def test_tap_state_resolution_rolls_back_atomically(self):
        session = self.session(7012604, players=2)
        engine = session.engine
        card = self.permanent(engine, "A", "Rollback Witness")
        program = SemanticProgram(
            key="test:invalid-typed-tap-sequence",
            label="Invalid typed tap sequence",
            effects=[
                {"op": "tap", "card": card.ref},
                {"op": "untap", "card": ""},
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="invalid-typed-tap-sequence",
                ref="S-invalid-typed-tap-sequence",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")

        first = session.act("pilot:A", {"action_id": "pass"})
        self.assertTrue(first.ok, first.summary)
        before = authoritative_state_hash(engine.state)
        second = session.act("pilot:B", {"action_id": "pass"})

        self.assertFalse(second.ok)
        self.assertIn("one nonempty target reference", second.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(engine.state.cards[card.object_id].tapped)
        self.assertEqual(
            ["S-invalid-typed-tap-sequence"],
            [item.ref for item in engine.state.stack],
        )

    def test_tap_state_effects_replay_exactly(self):
        session = self.session(7012605, players=2)
        engine = session.engine
        first_card = self.permanent(engine, "A", "Replay Tap Witness")
        second_card = self.permanent(
            engine, "B", "Replay Untap Witness", tapped=True
        )
        program = SemanticProgram(
            key="test:typed-tap-state-replay",
            label="Typed tap-state replay",
            effects=[
                {"op": "tap", "card": first_card.ref},
                {"op": "untap", "card": first_card.ref},
                {"op": "untap_all_creatures"},
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="typed-tap-state-replay",
                ref="S-typed-tap-state-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertFalse(first_card.tapped)
        self.assertFalse(second_card.tapped)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-tap-state-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_stack_draw_each_routes_through_handler_in_apnap_order(self):
        session = self.session(1210405)
        engine = session.engine
        program = SemanticProgram(
            key="test:typed-draw-each",
            label="Typed table draw",
            effects=[{"op": "draw_each_player", "count": 1}],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="typed-draw-each",
                ref="S-typed-draw-each",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B", "C"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        before = {
            seat: len(engine.state.players[seat].zones["hand"])
            for seat in engine.active_seats
        }

        original_lower = DrawEachPlayerHandler.lower
        with patch.object(
            DrawEachPlayerHandler,
            "lower",
            autospec=True,
            side_effect=original_lower,
        ) as lower:
            for principal in ("pilot:A", "pilot:B", "pilot:C"):
                result = session.act(principal, {"action_id": "pass"})
                self.assertTrue(result.ok, result.summary)

        self.assertGreaterEqual(lower.call_count, 1)
        self.assertEqual(
            {seat: count + 1 for seat, count in before.items()},
            {
                seat: len(engine.state.players[seat].zones["hand"])
                for seat in engine.active_seats
            },
        )

    def test_semantic_choice_preparation_draw_uses_replacement_coordinator(self):
        session = self.session(1210406, players=2)
        engine = session.engine
        loam = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Life from the Loam"
        )
        land = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Island"
        )
        engine.move_card(
            loam.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        engine.move_card(
            land.object_id,
            "hand",
            log=False,
            semantic_events=False,
        )
        program = SemanticProgram(
            key="test:replacement-aware-choice-draw",
            label="Draw, then choose a land",
            effects=[{"op": "draw_optional_land"}],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="replacement-aware-choice-draw",
                ref="S-replacement-aware-choice-draw",
                kind="triggered_ability",
                controller="B",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("B")
        engine._issue_priority("B")

        for principal in ("pilot:B", "pilot:A"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertEqual("draw.replacement", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": loam.ref,
                "reason": "Replace this effect draw with Dredge.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", loam.zone)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(["B"], engine.state.pending_decision.actors)


if __name__ == "__main__":
    unittest.main()
