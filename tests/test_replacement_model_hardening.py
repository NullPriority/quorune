from __future__ import annotations

import copy
import unittest

from quorune.replacement.operations import ReplacementOperationError
from quorune.replacement_effects import (
    AffectedObject,
    AddAmount,
    CreateAffectedObjectCounter,
    CreateAdditionalToken,
    MultiplyAmount,
    MultiplyTokenCreation,
    ReplaceableEvent,
    ReplacementClass,
    ReplacementContinuation,
    ReplacementEffect,
    ReplacementEffectError,
    ReplacementEventBatch,
    ReplacementSelection,
    SetField,
    advance_replacement_batch,
    apply_batch_replacement,
    apply_replacement,
    immutable_fingerprint,
    next_batch_replacement_choice,
    operation_from_dict,
    replacement_choice,
)


def effect(
    effect_id: str,
    event_kind: str,
    operation: dict,
    *,
    optional: bool = False,
) -> ReplacementEffect:
    return ReplacementEffect(
        effect_id=effect_id,
        source_id=f"source:{effect_id}",
        event_kind=event_kind,
        replacement_class=ReplacementClass.OTHER,
        operations=(operation,),
        optional=optional,
    )


class ReplacementImmutabilityTests(unittest.TestCase):
    def test_token_multiplier_operation_rejects_wrong_event_without_mutation(self):
        operation = MultiplyTokenCreation(
            factor=2,
            handler_id="replacement.token.quantity.v1",
            source_ref="multiplier-source",
        )
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))
        event = ReplaceableEvent(
            event_id="token-multiplier:wrong-event",
            kind="damage",
            affected_player="A",
            payload={"amount": 3},
        )
        replacement = ReplacementEffect(
            effect_id="token-multiplier:wrong-event",
            source_id="multiplier-source",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            operations=(operation,),
        )
        choice = replacement_choice(event, (replacement,))
        before = immutable_fingerprint(event.to_dict())
        with self.assertRaisesRegex(
            ReplacementEffectError, "token.create event"
        ):
            apply_replacement(
                choice, (replacement,), replacement.effect_id
            )
        self.assertEqual(before, immutable_fingerprint(event.to_dict()))

    def test_caller_mutation_cannot_change_event_effect_or_nested_values(self):
        payload = {"amount": 3, "metadata": {"values": [1, {"x": 2}]}}
        conditions = {"metadata": {"contains": "marker"}}
        operation = {
            "op": "set",
            "field": "prevented_by",
            "value": {"seats": ["A", "B"]},
        }
        event = ReplaceableEvent(
            event_id="immutable:event",
            kind="damage",
            affected_player="A",
            payload=payload,
        )
        replacement = ReplacementEffect(
            effect_id="immutable:effect",
            source_id="source:immutable",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions,
            operations=(operation,),
        )
        before_event = event.to_dict()
        before_effect = replacement.to_dict()

        payload["amount"] = 99
        payload["metadata"]["values"][1]["x"] = 77
        conditions["metadata"]["contains"] = "changed"
        operation["value"]["seats"].append("C")

        self.assertEqual(before_event, event.to_dict())
        self.assertEqual(before_effect, replacement.to_dict())
        with self.assertRaises(TypeError):
            event.payload["amount"] = 4  # type: ignore[index]
        with self.assertRaises(TypeError):
            replacement.conditions["new"] = True  # type: ignore[index]

    def test_canonical_fingerprint_ignores_mapping_construction_order(self):
        first = ReplaceableEvent(
            event_id="fingerprint:event",
            kind="damage",
            affected_player="A",
            payload={"amount": 3, "nested": {"b": 2, "a": 1}},
        )
        second = ReplaceableEvent(
            event_id="fingerprint:event",
            kind="damage",
            affected_player="A",
            payload={"nested": {"a": 1, "b": 2}, "amount": 3},
        )
        self.assertEqual(first, second)
        self.assertEqual(
            immutable_fingerprint(first.to_dict()),
            immutable_fingerprint(second.to_dict()),
        )

    def test_operations_lower_to_closed_typed_values_and_reject_drift(self):
        replacement = effect(
            "typed:multiply",
            "damage",
            {"op": "multiply", "field": "amount", "factor": 2},
        )
        self.assertIsInstance(replacement.operations[0], MultiplyAmount)
        self.assertEqual(
            {"op": "multiply", "field": "amount", "factor": 2},
            replacement.operations[0].to_dict(),
        )
        with self.assertRaisesRegex(
            ReplacementEffectError, "unknown future"
        ):
            effect(
                "typed:unknown-field",
                "damage",
                {
                    "op": "multiply",
                    "field": "amount",
                    "factor": 2,
                    "future": True,
                },
            )
        with self.assertRaisesRegex(
            ReplacementEffectError, "Unsupported replacement operation"
        ):
            effect("typed:unknown-op", "damage", {"op": "execute"})

    def test_unsupported_event_field_fails_without_mutating_event(self):
        event = ReplaceableEvent(
            event_id="rollback:event",
            kind="damage",
            affected_player="A",
            payload={"amount": 3},
        )
        replacement = ReplacementEffect(
            effect_id="rollback:field",
            source_id="source:rollback",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            operations=(SetField("library_order", ("secret",)),),
        )
        choice = replacement_choice(event, (replacement,))
        before = immutable_fingerprint(event.to_dict())
        with self.assertRaisesRegex(
            ReplacementEffectError, "does not support"
        ):
            apply_replacement(choice, (replacement,), replacement.effect_id)
        self.assertEqual(before, immutable_fingerprint(event.to_dict()))

    def test_affected_object_counter_is_typed_roundtrippable_and_post_transform(self):
        operation = CreateAffectedObjectCounter(
            counter_name="Void",
            amount=1,
            placing_player="B",
            source_ref="B-source",
        )
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))
        event = ReplaceableEvent(
            event_id="zone:affected-counter",
            kind="zone.change",
            affected_player=None,
            affected_object=AffectedObject("card:A", "A", "A"),
            payload={
                "origin": "battlefield",
                "destination": "graveyard",
                "destination_controller": None,
                "object_kind": "card",
                "object_ref": "A-card",
                "object_types": ["artifact"],
                "owner": "A",
            },
        )
        replacement = ReplacementEffect(
            effect_id="zone:replace-and-counter",
            source_id="B-source",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            operations=(
                SetField("destination", "exile"),
                operation,
            ),
        )
        choice = replacement_choice(event, (replacement,))
        resolved = apply_replacement(
            choice,
            (replacement,),
            replacement.effect_id,
        )

        self.assertEqual("exile", resolved.payload["destination"])
        self.assertEqual(1, len(resolved.children))
        self.assertEqual("exile", resolved.children[0].payload["target_zone"])
        self.assertEqual("void", resolved.children[0].payload["counter_name"])

    def test_affected_object_counter_rejects_malformed_event_without_mutation(self):
        event = ReplaceableEvent(
            event_id="zone:malformed-counter",
            kind="zone.change",
            affected_player=None,
            affected_object=AffectedObject("card:A", "A"),
            payload={"destination": "exile", "object_kind": "card"},
        )
        replacement = ReplacementEffect(
            effect_id="zone:malformed-counter-effect",
            source_id="B-source",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            operations=(
                CreateAffectedObjectCounter("void", 1, "B", "B-source"),
            ),
        )
        before = immutable_fingerprint(event.to_dict())

        with self.assertRaisesRegex(
            ReplacementEffectError,
            "public ref",
        ):
            apply_replacement(
                replacement_choice(event, (replacement,)),
                (replacement,),
                replacement.effect_id,
            )

        self.assertEqual(before, immutable_fingerprint(event.to_dict()))
        with self.assertRaisesRegex(
            ReplacementEffectError,
            "must be an integer",
        ):
            effect(
                "zone:boolean-counter",
                "zone.change",
                {
                    "op": "create_affected_object_counter",
                    "counter_name": "void",
                    "amount": True,
                    "placing_player": "B",
                    "source_ref": "B-source",
                    "sequence": 0,
                },
            )
        for field in ("counter_name", "placing_player", "source_ref"):
            malformed = CreateAffectedObjectCounter(
                "void", 1, "B", "B-source"
            ).to_dict()
            malformed[field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                ReplacementOperationError,
                "string",
            ):
                operation_from_dict(malformed)

    def test_additional_token_operation_is_immutable_roundtrippable_and_atomic(self):
        supplied = {
            "type_line": "Token Artifact — Food",
            "oracle_text": "{2}, {T}, Sacrifice this token: You gain 3 life.",
        }
        operation = CreateAdditionalToken(
            name="Food",
            quantity=1,
            characteristics=supplied,
            card_types=("artifact",),
            subtypes=("food",),
            handler_id="replacement.token.additional.v2",
            source_ref="A-source",
        )
        before = operation.to_dict()
        supplied["oracle_text"] = "mutated"
        self.assertEqual(before, operation.to_dict())
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))

        event = ReplaceableEvent(
            event_id="token:additional",
            kind="token.create",
            affected_player="A",
            payload={
                "event_controller": "A",
                "tokens": [{"name": "Cat", "quantity": 1}],
                "created_types": ["creature"],
                "created_subtypes": ["cat"],
            },
        )
        replacement = ReplacementEffect(
            effect_id="token:add-food",
            source_id="A-source",
            event_kind="token.create",
            replacement_class=ReplacementClass.OTHER,
            operations=(operation,),
        )
        original_fingerprint = immutable_fingerprint(event.to_dict())
        resolved = apply_replacement(
            replacement_choice(event, (replacement,)),
            (replacement,),
            replacement.effect_id,
        )
        self.assertEqual(
            ("artifact", "creature"),
            resolved.payload["created_types"],
        )
        self.assertEqual(
            ("cat", "food"),
            resolved.payload["created_subtypes"],
        )
        self.assertEqual(2, len(resolved.payload["tokens"]))
        self.assertEqual(
            "replacement.token.additional.v2",
            resolved.payload["tokens"][1]["replacement_component"]["handler_id"],
        )
        self.assertEqual(
            original_fingerprint,
            immutable_fingerprint(event.to_dict()),
        )

    def test_additional_token_operation_rejects_wrong_event_without_mutation(self):
        event = ReplaceableEvent(
            event_id="damage:not-token",
            kind="damage",
            affected_player="A",
            payload={"amount": 1},
        )
        operation = CreateAdditionalToken(
            name="Food",
            quantity=1,
            characteristics={"type_line": "Token Artifact — Food"},
            card_types=("artifact",),
            subtypes=("food",),
            handler_id="replacement.token.additional.v2",
            source_ref="A-source",
        )
        replacement = ReplacementEffect(
            effect_id="damage:add-food",
            source_id="A-source",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            operations=(operation,),
        )
        before = immutable_fingerprint(event.to_dict())
        with self.assertRaisesRegex(
            ReplacementEffectError, "token.create"
        ):
            apply_replacement(
                replacement_choice(event, (replacement,)),
                (replacement,),
                replacement.effect_id,
            )
        self.assertEqual(before, immutable_fingerprint(event.to_dict()))

        malformed = operation.to_dict()
        malformed["quantity"] = True
        with self.assertRaisesRegex(
            ReplacementOperationError, "must be an integer"
        ):
            operation_from_dict(malformed)


class ReplacementTreeValidationTests(unittest.TestCase):
    @staticmethod
    def nested_root(
        root_id: str,
        *,
        root_chooser: str,
        child_chooser: str,
    ) -> ReplaceableEvent:
        return ReplaceableEvent(
            event_id=root_id,
            kind="effect",
            affected_player=root_chooser,
            payload={"resolved": False},
            children=(
                ReplaceableEvent(
                    event_id=f"{root_id}:child",
                    kind="counter.add",
                    affected_player=child_chooser,
                    payload={"amount": 1},
                ),
            ),
        )

    def test_nested_chooser_is_validated_during_construction_and_restore(self):
        malformed = self.nested_root(
            "nested:bad", root_chooser="A", child_chooser="E"
        )
        with self.assertRaisesRegex(
            ReplacementEffectError, "missing from APNAP order: E"
        ):
            ReplacementEventBatch(
                batch_id="nested:bad-batch",
                events=(malformed,),
                apnap_order=("A", "B", "C", "D"),
            )

        valid = ReplacementEventBatch(
            batch_id="nested:restore",
            events=(
                self.nested_root(
                    "nested:restore-root",
                    root_chooser="A",
                    child_chooser="B",
                ),
            ),
            apnap_order=("A", "B", "C", "D"),
        ).to_dict()
        valid["events"][0]["children"][0]["affected_player"] = "E"
        with self.assertRaisesRegex(
            ReplacementEffectError, "missing from APNAP order: E"
        ):
            ReplacementEventBatch.from_dict(valid)

    def test_affected_object_controller_and_owner_fallback_are_valid(self):
        controlled = ReplaceableEvent(
            event_id="object:controlled",
            kind="counter.add",
            affected_player=None,
            affected_object=AffectedObject("permanent:1", "B", "C"),
            payload={"amount": 1},
        )
        owner_only = ReplaceableEvent(
            event_id="object:owner",
            kind="counter.add",
            affected_player=None,
            affected_object=AffectedObject("card:2", "B"),
            payload={"amount": 1},
        )
        batch = ReplacementEventBatch(
            batch_id="objects:valid",
            events=(controlled, owner_only),
            apnap_order=("A", "B", "C", "D"),
        )
        self.assertEqual("C", batch.events[0].chooser)
        self.assertEqual("B", batch.events[1].chooser)

    def test_four_player_nested_choices_use_nested_chooser_apnap_order(self):
        batch = ReplacementEventBatch(
            batch_id="nested:apnap",
            events=(
                self.nested_root(
                    "nested:C", root_chooser="A", child_chooser="C"
                ),
                self.nested_root(
                    "nested:B", root_chooser="D", child_chooser="B"
                ),
            ),
            apnap_order=("B", "C", "D", "A"),
        )
        replacements = (
            effect(
                "counter:add-one",
                "counter.add",
                {"op": "add", "field": "amount", "amount": 1},
            ),
        )
        first = next_batch_replacement_choice(batch, replacements)
        self.assertEqual("B", first.choice.chooser)
        batch = apply_batch_replacement(
            batch, replacements, first, first.choice.options[0]
        )
        second = next_batch_replacement_choice(batch, replacements)
        self.assertEqual("C", second.choice.chooser)

    def test_tampered_journal_path_and_unknown_fields_fail_closed(self):
        batch = ReplacementEventBatch(
            batch_id="journal:valid",
            events=(
                self.nested_root(
                    "journal:root", root_chooser="A", child_chooser="B"
                ),
            ),
            apnap_order=("A", "B", "C", "D"),
        ).to_dict()
        batch["journal"] = [
            {
                "event_id": "journal:root",
                "path": [9],
                "chooser": "B",
                "effect_id": "tampered",
            }
        ]
        with self.assertRaisesRegex(
            ReplacementEffectError, "path is no longer valid"
        ):
            ReplacementEventBatch.from_dict(batch)

        applied = self.nested_root(
            "journal:applied", root_chooser="A", child_chooser="B"
        ).to_dict()
        applied["children"][0]["applied_effects"] = ["expected"]
        wrong_effect = ReplacementEventBatch(
            batch_id="journal:wrong-effect",
            events=(ReplaceableEvent.from_dict(applied),),
            apnap_order=("A", "B", "C", "D"),
        ).to_dict()
        wrong_effect["journal"] = [
            {
                "event_id": "journal:applied",
                "path": [0],
                "chooser": "B",
                "effect_id": "not-applied",
            }
        ]
        with self.assertRaisesRegex(
            ReplacementEffectError, "not applied at its event path"
        ):
            ReplacementEventBatch.from_dict(wrong_effect)

        event = ReplaceableEvent(
            event_id="unknown:event",
            kind="damage",
            affected_player="A",
            payload={"amount": 1},
        ).to_dict()
        event["future"] = True
        with self.assertRaisesRegex(
            ReplacementEffectError, "unknown future"
        ):
            ReplaceableEvent.from_dict(event)

    def test_optional_decline_is_always_journaled_canonically(self):
        event = ReplaceableEvent(
            event_id="decline:event",
            kind="damage",
            affected_player="A",
            payload={"amount": 1},
        )
        optional = effect(
            "optional:prevent",
            "damage",
            {"op": "prevent", "amount": 1},
            optional=True,
        )
        batch = ReplacementEventBatch(
            batch_id="decline:batch",
            events=(event,),
            apnap_order=("A", "B", "C", "D"),
        )
        pending = next_batch_replacement_choice(batch, (optional,))
        resolved = apply_batch_replacement(
            batch, (optional,), pending, None
        )
        self.assertEqual(
            "decline:optional:prevent", resolved.journal[0].effect_id
        )
        self.assertEqual(
            resolved,
            ReplacementEventBatch.from_dict(resolved.to_dict()),
        )
        with self.assertRaisesRegex(
            ReplacementEffectError, "canonical strings"
        ):
            ReplacementSelection(
                event_id="decline:event",
                path=(),
                chooser="A",
                effect_id=None,  # type: ignore[arg-type]
            )

    def test_nested_event_payload_and_effect_scalars_fail_closed(self):
        parent = ReplaceableEvent(
            event_id="strict:parent",
            kind="damage",
            affected_player="A",
            payload={"amount": 1},
        )
        malformed_nested = effect(
            "strict:nested",
            "damage",
            {
                "op": "nested_event",
                "event": {
                    "kind": "life.change",
                    "payload": [],
                },
            },
        )
        with self.assertRaisesRegex(
            ReplacementEffectError, "payload must be an object"
        ):
            apply_replacement(
                replacement_choice(parent, (malformed_nested,)),
                (malformed_nested,),
                malformed_nested.effect_id,
            )

        serialized = effect(
            "strict:effect",
            "damage",
            {"op": "prevent", "amount": 1},
        ).to_dict()
        serialized["optional"] = "false"
        with self.assertRaisesRegex(
            ReplacementEffectError, "optional must be a boolean"
        ):
            ReplacementEffect.from_dict(serialized)

        serialized = effect(
            "strict:class",
            "damage",
            {"op": "prevent", "amount": 1},
        ).to_dict()
        serialized["replacement_class"] = 999
        with self.assertRaisesRegex(
            ReplacementEffectError, "invalid replacement class"
        ):
            ReplacementEffect.from_dict(serialized)


class ReplacementContinuationTests(unittest.TestCase):
    @staticmethod
    def continuation() -> dict:
        event = ReplaceableEvent(
            event_id="continuation:event",
            kind="damage",
            affected_player="A",
            payload={"amount": 1},
        )
        replacement = effect(
            "continuation:prevent",
            "damage",
            {"op": "prevent", "amount": 1},
            optional=True,
        )
        return {
            "replacement_resume_kind": "combat_damage",
            "combat_assignments": [
                {"source": "A01", "target": "B", "amount": 1}
            ],
            "replacement_selections": [
                "decline:continuation:prevent"
            ],
            "replacement_batch": ReplacementEventBatch(
                batch_id="continuation:batch",
                events=(event,),
                apnap_order=("A", "B", "C", "D"),
            ).to_dict(),
            "replacement_effects": [replacement.to_dict()],
        }

    @classmethod
    def mana_continuation(cls) -> dict:
        value = cls.continuation()
        value.pop("combat_assignments")
        value.pop("replacement_selections")
        value.update(
            {
                "replacement_resume_kind": "mana_payment",
                "priority_seat": "A",
                "priority_action": "activate",
                "priority_response": {
                    "source": "A01",
                    "ability": "ab1",
                    "_mana_payment_id": "D-mana-payment",
                },
                "priority_frame": {
                    "active_player": "A",
                    "phase": "precombat_main",
                    "step": "main",
                    "turn_sequence": 2,
                    "priority_player": "A",
                    "priority_epoch": 7,
                    "stack_refs": [],
                },
            }
        )
        return value

    @classmethod
    def semantic_counter_continuation(cls) -> dict:
        value = cls.continuation()
        value.pop("combat_assignments")
        value.update(
            {
                "replacement_resume_kind": "semantic_counter_completion",
                "semantic_choice_continuation": {
                    "schema_version": 2,
                    "handler_id": "choice.token.fabricate.v1",
                },
                "semantic_choice_actor": "A",
                "semantic_choice_response": {"choice": "counter"},
                "intent_index": 0,
                "counter_intent": {
                    "actor": "A",
                    "object_refs": ["A01"],
                    "counter_name": "+1/+1",
                    "amount": 1,
                    "reason": "fabricate",
                    "source_ref": "A01",
                },
            }
        )
        return value

    def test_continuation_rejects_malformed_entries_and_unknown_fields(self):
        value = self.continuation()
        restored = ReplacementContinuation.from_dict(value)
        self.assertEqual(
            ("decline:continuation:prevent",),
            restored.replacement_selections,
        )

        malformed = copy.deepcopy(value)
        malformed["replacement_effects"].append(42)
        with self.assertRaisesRegex(
            ReplacementEffectError, "contain only objects"
        ):
            ReplacementContinuation.from_dict(malformed)

        malformed = copy.deepcopy(value)
        malformed["replacement_selections"] = [None]
        with self.assertRaisesRegex(
            ReplacementEffectError, "canonical strings"
        ):
            ReplacementContinuation.from_dict(malformed)

        malformed = copy.deepcopy(value)
        malformed["future"] = True
        with self.assertRaisesRegex(
            ReplacementEffectError, "unknown future"
        ):
            ReplacementContinuation.from_dict(malformed)

    def test_mana_continuation_is_strict_deep_frozen_and_typed(self):
        value = self.mana_continuation()
        restored = ReplacementContinuation.from_dict(value)
        value["priority_response"]["source"] = "tampered"
        value["priority_frame"]["phase"] = "combat"
        self.assertEqual("A01", restored.thaw_priority_response()["source"])
        self.assertEqual(
            "precombat_main", restored.thaw_priority_frame()["phase"]
        )

        for label, mutate, message in (
            (
                "unknown frame field",
                lambda row: row["priority_frame"].update({"future": True}),
                "unknown future",
            ),
            (
                "boolean sequence",
                lambda row: row["priority_frame"].update(
                    {"turn_sequence": True}
                ),
                "frame values",
            ),
            (
                "lost payment identity",
                lambda row: row["priority_response"].pop(
                    "_mana_payment_id"
                ),
                "stable payment identity",
            ),
            (
                "wrong priority principal",
                lambda row: row["priority_frame"].update(
                    {"priority_player": "B"}
                ),
                "frame values",
            ),
            (
                "malformed activation",
                lambda row: row["priority_response"].pop("ability"),
                "activation response",
            ),
        ):
            with self.subTest(label=label):
                malformed = self.mana_continuation()
                mutate(malformed)
                with self.assertRaisesRegex(
                    ReplacementEffectError, message
                ):
                    ReplacementContinuation.from_dict(malformed)

    def test_semantic_counter_continuation_is_strict_and_deep_frozen(self):
        value = self.semantic_counter_continuation()
        restored = ReplacementContinuation.from_dict(value)
        value["semantic_choice_response"]["choice"] = "token"
        value["counter_intent"]["amount"] = 9
        self.assertEqual(
            "counter", restored.thaw_semantic_choice_response()["choice"]
        )
        self.assertEqual(1, restored.thaw_counter_intent()["amount"])

        for label, mutate, message in (
            (
                "boolean index",
                lambda row: row.update({"intent_index": True}),
                "fields are malformed",
            ),
            (
                "empty actor",
                lambda row: row.update({"semantic_choice_actor": ""}),
                "fields are malformed",
            ),
            (
                "nonmapping intent",
                lambda row: row.update({"counter_intent": []}),
                "fields are malformed",
            ),
            (
                "unknown field",
                lambda row: row.update({"future": True}),
                "unknown future",
            ),
        ):
            with self.subTest(label=label):
                malformed = self.semantic_counter_continuation()
                mutate(malformed)
                with self.assertRaisesRegex(
                    ReplacementEffectError, message
                ):
                    ReplacementContinuation.from_dict(malformed)

if __name__ == "__main__":
    unittest.main()
