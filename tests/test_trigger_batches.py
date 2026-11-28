from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest

from common import keep_all, load_assets, make_session
from quorune.errors import GameRuleError
from quorune.model import GameConfig, GameState
from quorune.semantics import SemanticProgram
from quorune.trigger_batches import (
    PendingTriggerBatch,
    PendingTriggerItem,
    TriggerBatchError,
    begin_pending_trigger_placement,
    complete_pending_trigger_group,
    create_pending_trigger_batch,
    equivalent_untargeted_trigger_refs,
    merge_pending_trigger_batch,
)
from quorune.trigger_processing import (
    collect_trigger_items,
    enqueue_trigger_batch,
)


def trigger_payload(
    ref: str,
    controller: str,
    *,
    label: str | None = None,
    context: dict | None = None,
) -> dict:
    return {
        "stack_id": f"stack-{ref}",
        "ref": ref,
        "kind": "triggered_ability",
        "controller": controller,
        "label": label or ref,
        "visibility": ["A", "B", "C", "D"],
        "context": context or {},
    }


class TriggerBatchModelTests(unittest.TestCase):
    def test_identical_order_uses_exact_source_and_context_and_retains_copy_audit(self):
        program = SimpleNamespace(
            key="fixture:trigger", trust_level="trusted", requires_arbiter=False,
            handlers=(), target_schema=None, cost_schema=None,
            effects=({"op": "life", "player": "$controller", "delta": 1},),
            event_condition=None,
        )
        context = {"event": "permanent.enter", "source_logical_object_id": "source@2", "amount": 1}
        base = trigger_payload("S1", "A", label="Same ability", context=context)
        base.update(source_object_id="source", semantic_key=program.key)
        copy_row = {**base, "ref": "S2", "stack_id": "stack-S2",
                    "context": {**context, "additional_trigger_copy": 1,
                                "additional_trigger_source": {"schema_version": 1}}}
        first = PendingTriggerItem.from_dict(base)
        second = PendingTriggerItem.from_dict(copy_row)
        self.assertEqual(["S1", "S2"], equivalent_untargeted_trigger_refs((first, second), program))
        self.assertEqual(copy_row, second.to_dict())

        for changed in (
            {**copy_row, "source_object_id": "another-source"},
            {**copy_row, "context": {**context, "source_logical_object_id": "source@3"}},
            {**copy_row, "context": {**context, "amount": True}},
            {**copy_row, "context": {**context, "trigger_target_selection_pending": True}},
            {**copy_row, "context": {**context, "additional_trigger_copy": None}},
            {**copy_row, "targets": ["B"]},
            {**copy_row, "visibility": ["A"]},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(equivalent_untargeted_trigger_refs(
                    (first, PendingTriggerItem.from_dict(changed)), program,
                ))
        program.effects = ({"op": "life", "delta": "$context.additional_trigger_copy"},)
        self.assertIsNone(equivalent_untargeted_trigger_refs((first, second), program))
        program.effects = ({"op": "counter_stack", "target": "$stack"},)
        self.assertIsNone(equivalent_untargeted_trigger_refs((first, second), program))

    def test_identical_order_config_preserves_absence_for_old_replay(self):
        new = GameConfig().to_dict()
        self.assertEqual(1, new["identical_trigger_order_version"])
        old = dict(new)
        old.pop("identical_trigger_order_version")
        restored = GameConfig.from_dict(old)
        self.assertIsNone(restored.identical_trigger_order_version)
        self.assertEqual(old, restored.to_dict())
        for invalid in (True, 0, 1.0, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                GameConfig.from_dict({**new, "identical_trigger_order_version": invalid})

    def test_pending_item_deep_freezes_caller_data(self):
        payload = trigger_payload(
            "S1",
            "A",
            context={"nested": {"values": [1, 2]}},
        )
        item = PendingTriggerItem.from_dict(payload)

        payload["controller"] = "B"
        payload["context"]["nested"]["values"].append(3)

        self.assertEqual("A", item.controller)
        self.assertEqual(
            [1, 2],
            item.to_dict()["context"]["nested"]["values"],
        )
        self.assertEqual(
            item,
            PendingTriggerItem.from_dict(
                {
                    "context": {"nested": {"values": [1, 2]}},
                    "visibility": ["A", "B", "C", "D"],
                    "label": "S1",
                    "controller": "A",
                    "kind": "triggered_ability",
                    "ref": "S1",
                    "stack_id": "stack-S1",
                }
            ),
        )

    def test_pending_item_rejects_malformed_nested_values(self):
        malformed_values = (
            {"targets": [4]},
            {"modes": [""]},
            {"visibility": ["A", "A"]},
            {"referred_object_ids": [""]},
            {"x_value": True},
        )
        for update in malformed_values:
            with self.subTest(update=update):
                payload = trigger_payload("S1", "A")
                payload.update(update)
                with self.assertRaises(TriggerBatchError):
                    PendingTriggerItem.from_dict(payload)

    def test_legacy_batch_shape_round_trips_to_versioned_shape(self):
        batch = create_pending_trigger_batch(
            batch_id="batch-1",
            ref="TB1",
            items=[trigger_payload("S1", "A")],
            apnap_order=("A", "B"),
            turn_sequence=3,
            priority_epoch=7,
        )
        legacy = batch.to_dict()
        legacy.pop("schema_version")

        restored = PendingTriggerBatch.from_dict(legacy)

        self.assertEqual(batch, restored)
        self.assertEqual(1, restored.to_dict()["schema_version"])

    def test_batch_deserialization_rejects_unknown_and_malformed_entries(self):
        valid = create_pending_trigger_batch(
            batch_id="batch-1",
            ref="TB1",
            items=[trigger_payload("S1", "A")],
            apnap_order=("A", "B"),
            turn_sequence=3,
            priority_epoch=7,
        ).to_dict()
        unknown = copy.deepcopy(valid)
        unknown["arbitrary"] = True
        malformed_item = copy.deepcopy(valid)
        malformed_item["groups"][0]["items"][0] = "not-a-mapping"
        malformed_started = copy.deepcopy(valid)
        malformed_started["placement_started"] = 1

        for value in (unknown, malformed_item, malformed_started):
            with self.subTest(value=value):
                with self.assertRaises(TriggerBatchError):
                    PendingTriggerBatch.from_dict(value)

    def test_batch_merges_only_before_placement_starts(self):
        batch = create_pending_trigger_batch(
            batch_id="batch-1",
            ref="TB1",
            items=[trigger_payload("S1", "A")],
            apnap_order=("A", "B", "C", "D"),
            turn_sequence=3,
            priority_epoch=7,
        )
        merged = merge_pending_trigger_batch(
            batch,
            [trigger_payload("S2", "B")],
            apnap_order=("A", "B", "C", "D"),
            priority_epoch=7,
        )
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertEqual(["A", "B"], [group.controller for group in merged.groups])

        started = begin_pending_trigger_placement(
            merged,
            apnap_order=("A", "B", "C", "D"),
        )
        assert started is not None
        self.assertTrue(started.placement_started)
        self.assertIsNone(
            merge_pending_trigger_batch(
                started,
                [trigger_payload("S3", "C")],
                apnap_order=("A", "B", "C", "D"),
                priority_epoch=7,
            )
        )
        placed, remaining = complete_pending_trigger_group(
            started,
            controller="A",
            refs=("S1",),
        )
        self.assertEqual(("S1",), tuple(item.ref for item in placed))
        self.assertIsNotNone(remaining)

    def test_inactive_controller_is_dropped_only_at_placement(self):
        batch = create_pending_trigger_batch(
            batch_id="batch-1",
            ref="TB1",
            items=[
                trigger_payload("S1", "A"),
                trigger_payload("S2", "B"),
            ],
            apnap_order=("A", "B"),
            turn_sequence=3,
            priority_epoch=7,
        )

        started = begin_pending_trigger_placement(
            batch,
            apnap_order=("A",),
        )

        assert started is not None
        self.assertEqual(("A",), started.apnap_order)
        self.assertEqual(("S1",), tuple(item.ref for item in started.items))


class UnifiedTriggerBatchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_engine(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        engine.state.active_player = "A"
        return engine

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def test_semantic_and_delayed_occurrences_share_one_apnap_batch(self):
        engine = self.make_engine(60320)
        source = self.card(engine, "A", "Sensei's Divining Top")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.semantics.put(
            SemanticProgram(
                key=f"{source.oracle_id}:test:unified-trigger-batch",
                label="Static-source occurrence",
                oracle_id=source.oracle_id,
                ability_id="test:unified-trigger-batch",
                active_zone="battlefield",
                event="test.trigger.batch",
                effects=[],
            )
        )
        engine.schedule_delayed_trigger(
            controller="B",
            label="Delayed occurrence",
            event_kind="test.trigger.batch",
            condition={"player": "A"},
            stack_template={"label": "Delayed occurrence"},
        )

        occurrences = collect_trigger_items(
            engine,
            "test.trigger.batch",
            {"player": "A"},
        )
        enqueue_trigger_batch(engine, occurrences)

        self.assertEqual(2, len(occurrences))
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(
            ["A", "B"],
            [group.controller for group in batch.groups],
        )
        self.assertEqual(
            {"Static-source occurrence", "Delayed occurrence"},
            {item.label for item in batch.items},
        )

        checkpoint = engine.state.to_dict()
        restored = GameState.from_dict(checkpoint)
        self.assertEqual(checkpoint, restored.to_dict())

        engine._grant_priority("A")

        self.assertEqual(
            ["Static-source occurrence", "Delayed occurrence"],
            [item.label for item in engine.state.stack],
        )
        self.assertEqual("A", engine.state.priority_player)
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_malformed_checkpoint_batch_fails_without_mutating_live_state(self):
        engine = self.make_engine(60321)
        before = engine.state.to_dict()
        malformed = copy.deepcopy(before)
        malformed["pending_trigger_batches"] = [
            {
                "schema_version": 1,
                "batch_id": "batch-1",
                "ref": "TB1",
                "apnap_order": ["A", "B"],
                "groups": [None],
                "turn_sequence": 1,
                "priority_epoch": 1,
                "placement_started": False,
            }
        ]

        with self.assertRaises(TriggerBatchError):
            GameState.from_dict(malformed)

        self.assertEqual(before, engine.state.to_dict())

    def test_same_controller_uses_one_order_continuation_for_all_sources(self):
        engine = self.make_engine(60322)
        source = self.card(engine, "A", "Sensei's Divining Top")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.semantics.put(
            SemanticProgram(
                key=f"{source.oracle_id}:test:unified-trigger-order",
                label="Static-source occurrence",
                oracle_id=source.oracle_id,
                ability_id="test:unified-trigger-order",
                active_zone="battlefield",
                event="test.trigger.order",
                effects=[],
            )
        )
        engine.schedule_delayed_trigger(
            controller="A",
            label="Delayed occurrence",
            event_kind="test.trigger.order",
            condition={},
            stack_template={"label": "Delayed occurrence"},
        )
        occurrences = collect_trigger_items(
            engine,
            "test.trigger.order",
            {},
        )
        by_label = {item.label: item.ref for item in occurrences}
        enqueue_trigger_batch(engine, occurrences)

        engine._grant_priority("A")

        decision = engine.state.pending_decision
        assert decision is not None
        self.assertEqual("trigger.order", decision.kind)
        self.assertEqual(
            {"trigger_batch_id", "trigger_refs"},
            set(decision.continuation),
        )
        self.assertNotIn("trigger_ids", decision.continuation)
        self.assertNotIn("groups", decision.continuation)
        self.assertTrue(
            engine.state.pending_trigger_batches[0].placement_started
        )
        checkpoint = engine.state.to_dict()
        self.assertEqual(checkpoint, GameState.from_dict(checkpoint).to_dict())

        decision.responses["A"] = {
            "triggers": [
                by_label["Delayed occurrence"],
                by_label["Static-source occurrence"],
            ]
        }
        engine._complete_trigger_order(decision)

        self.assertEqual(
            ["Delayed occurrence", "Static-source occurrence"],
            [item.label for item in engine.state.stack],
        )
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_historical_semantic_batch_continuation_is_explicitly_compatible(self):
        engine = self.make_engine(60323)
        first = PendingTriggerItem.from_dict(
            trigger_payload("S-historical-1", "A", label="First")
        )
        second = PendingTriggerItem.from_dict(
            trigger_payload("S-historical-2", "A", label="Second")
        )
        engine.state.pending_trigger_batches.append(
            begin_pending_trigger_placement(
                create_pending_trigger_batch(
                    batch_id="historical-batch",
                    ref="TB-historical",
                    items=(first, second),
                    apnap_order=("A", "B"),
                    turn_sequence=engine.state.turn_sequence,
                    priority_epoch=engine.state.priority_epoch,
                ),
                apnap_order=("A", "B"),
            )
        )
        decision = SimpleNamespace(
            actors=["A"],
            responses={
                "A": {
                    "triggers": ["S-historical-2", "S-historical-1"]
                }
            },
            continuation={
                "semantic_trigger_batch_id": "historical-batch",
                "trigger_refs": ["S-historical-1", "S-historical-2"],
            },
        )

        engine._complete_trigger_order(decision)

        self.assertEqual(["Second", "First"], [item.label for item in engine.state.stack])

    def test_historical_delayed_order_validates_tree_before_stack_mutation(self):
        engine = self.make_engine(60324)
        first = engine.schedule_delayed_trigger(
            controller="A",
            label="Historical first",
            event_kind="test.historical",
            condition={},
            stack_template={"label": "Historical first"},
        )
        second = engine.schedule_delayed_trigger(
            controller="A",
            label="Historical second",
            event_kind="test.historical",
            condition={},
            stack_template={"label": "Historical second"},
        )
        before = engine.state.to_dict()
        malformed = SimpleNamespace(
            actors=["A"],
            responses={"A": {"triggers": [second.ref, first.ref]}},
            continuation={
                "groups": [
                    {
                        "controller": "B",
                        "trigger_ids": ["missing-trigger"],
                    }
                ],
                "after": "grant_priority",
                "trigger_ids": [first.trigger_id, second.trigger_id],
            },
        )

        with self.assertRaises(GameRuleError):
            engine._complete_trigger_order(malformed)
        self.assertEqual(before, engine.state.to_dict())

        compatible = SimpleNamespace(
            actors=["A"],
            responses={"A": {"triggers": [second.ref, first.ref]}},
            continuation={
                "groups": [],
                "after": "grant_priority",
                "trigger_ids": [first.trigger_id, second.trigger_id],
            },
        )
        engine._complete_trigger_order(compatible)

        self.assertEqual(
            ["Historical second", "Historical first"],
            [item.label for item in engine.state.stack],
        )


if __name__ == "__main__":
    unittest.main()
