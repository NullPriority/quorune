from __future__ import annotations

from dataclasses import dataclass
import unittest

from quorune.replacement.immutable import FrozenMap
from quorune.semantic_choices import (
    AutoContinue,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
    SemanticChoicePreparation,
    SemanticChoiceRegistry,
    SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class _ChoiceHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 608.2c",)
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = ("choice",)
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions")
    mutation_path: tuple[str, ...] = ("semantic intent executor",)
    replay_fixture: str = "semantic-choice-model"
    test_modules: tuple[str, ...] = ("tests.test_semantic_choice_model",)

    def prepare(self, effect, context):
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose one.",
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=("one", "two"),
                ),
            ),
            continuation_effect=FrozenMap(effect),
        )

    def complete(self, continuation, response, query):
        return SemanticChoiceCompletion()


def _frame() -> SemanticChoiceFrame:
    return SemanticChoiceFrame(
        semantic_program_id="program:test",
        semantic_program_version=1,
        stack_object="S1",
        instruction_pointer=3,
        controller="A",
    )


def _continuation(
    *,
    handler_id: str = "choice.test.v1",
) -> SemanticChoiceContinuation:
    return SemanticChoiceContinuation(
        handler_id=handler_id,
        handler_version=1,
        stack_ref="S1",
        effect=FrozenMap({"op": "choose_test", "values": ["one", "two"]}),
        remaining=(FrozenMap({"op": "draw", "count": 1}),),
        destination="graveyard",
        note="model test",
        semantic_frame=_frame(),
    )


class SemanticChoiceModelTests(unittest.TestCase):
    def test_continuation_deep_freezes_caller_owned_values(self):
        effect = {"op": "choose_test", "values": ["one", "two"]}
        remaining = [{"op": "draw", "count": 1}]
        continuation = SemanticChoiceContinuation(
            handler_id="choice.test.v1",
            handler_version=1,
            stack_ref="S1",
            effect=FrozenMap(effect),
            remaining=tuple(FrozenMap(value) for value in remaining),
            destination="graveyard",
            note="immutable",
            semantic_frame=_frame(),
        )

        effect["values"].append("three")
        remaining[0]["count"] = 99

        self.assertEqual(["one", "two"], continuation.to_dict()["effect"]["values"])
        self.assertEqual(1, continuation.to_dict()["remaining"][0]["count"])

    def test_new_continuation_round_trip_is_exact(self):
        continuation = _continuation()
        self.assertEqual(
            continuation,
            SemanticChoiceContinuation.from_dict(continuation.to_dict()),
        )

    def test_legacy_continuation_requires_pinned_handler(self):
        legacy = _continuation().to_dict()
        for field in ("schema_version", "handler_id", "handler_version"):
            legacy.pop(field)
        with self.assertRaisesRegex(
            SemanticChoiceError,
            "requires a pinned handler",
        ):
            SemanticChoiceContinuation.from_dict(legacy)
        decoded = SemanticChoiceContinuation.from_dict(
            legacy,
            legacy_handler_id="choice.test.v1",
            legacy_handler_version=1,
        )
        self.assertEqual("choice.test.v1", decoded.handler_id)

    def test_unknown_continuation_field_fails_closed(self):
        serialized = _continuation().to_dict()
        serialized["unexpected"] = True
        with self.assertRaisesRegex(SemanticChoiceError, "unknown fields"):
            SemanticChoiceContinuation.from_dict(serialized)

    def test_malformed_remaining_entry_fails_closed(self):
        serialized = _continuation().to_dict()
        serialized["remaining"].append("not an effect")
        with self.assertRaisesRegex(
            SemanticChoiceError,
            "list of mappings",
        ):
            SemanticChoiceContinuation.from_dict(serialized)

    def test_common_scalar_model_preserves_legacy_action_shape(self):
        request = SemanticChoiceRequest(
            prompt="Choose a mana color.",
            choice=ScalarChoice(
                field_name="choice",
                legal_values=("W", "U", "B", "R", "G", "C"),
            ),
            public_context=FrozenMap(
                {"stack": "S1", "operation": "choose_mana"}
            ),
        )
        payload = request.payload()
        self.assertEqual("S1", payload["stack"])
        self.assertEqual(
            ["W", "U", "B", "R", "G", "C"],
            payload["legal_actions"][0]["choice_schema"]["legal_values"],
        )

    def test_object_choice_disjoint_cardinalities_preserve_legacy_ranges(self):
        from quorune.semantic_choices.model import ObjectChoice
        legacy = ObjectChoice(field_name='cards', legal_refs=('A01','A02'), zones=('hand',), minimum=0, maximum=2, optional=True)
        self.assertNotIn('allowed_cardinalities',legacy.choice_schema())
        exact = ObjectChoice(field_name='cards', legal_refs=('A01','A02'), zones=('hand',), minimum=0, maximum=2, optional=True, allowed_cardinalities=(0,2))
        self.assertEqual([0,2],exact.choice_schema()['allowed_cardinalities'])
        for counts in ((),(0,True),(2,0),(0,1),(0,2,3),(0,0,2)):
            with self.subTest(counts=counts),self.assertRaises(SemanticChoiceError):
                ObjectChoice(field_name='cards',legal_refs=('A01','A02'),zones=('hand',),minimum=0,maximum=2,optional=True,allowed_cardinalities=counts)

    def test_preparation_requires_choice_or_explicit_auto_continue(self):
        with self.assertRaisesRegex(SemanticChoiceError, "issue a request"):
            SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap({"op": "choose_test"}),
            )
        preparation = SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap({"op": "choose_test"}),
            auto_continue=AutoContinue(reason="no legal option"),
        )
        self.assertEqual("no legal option", preparation.auto_continue.reason)


class SemanticChoiceRegistryTests(unittest.TestCase):
    def test_one_operation_has_one_handler(self):
        registry = SemanticChoiceRegistry(
            [_ChoiceHandler("choose_test", "choice.test.v1")]
        )
        with self.assertRaisesRegex(SemanticChoiceError, "Duplicate.*operation"):
            registry.register(
                _ChoiceHandler("choose_test", "choice.other.v1")
            )

    def test_handler_owns_prepare_and_complete_and_registry_freezes(self):
        registry = SemanticChoiceRegistry(
            [_ChoiceHandler("choose_test", "choice.test.v1")]
        ).freeze()
        self.assertEqual(("choose_test",), registry.operations)
        self.assertEqual(64, len(registry.fingerprint))
        with self.assertRaisesRegex(SemanticChoiceError, "frozen"):
            registry.register(
                _ChoiceHandler("choose_other", "choice.other.v1")
            )

    def test_old_continuation_uses_operation_compatibility_decoder(self):
        registry = SemanticChoiceRegistry(
            [_ChoiceHandler("choose_test", "choice.test.v1")]
        ).freeze()
        legacy = _continuation().to_dict()
        for field in ("schema_version", "handler_id", "handler_version"):
            legacy.pop(field)
        handler, continuation = registry.decode_continuation(legacy)
        self.assertEqual("choice.test.v1", handler.handler_id)
        self.assertEqual(handler.handler_id, continuation.handler_id)

    def test_handler_version_is_replay_pinned(self):
        registry = SemanticChoiceRegistry(
            [_ChoiceHandler("choose_test", "choice.test.v1", schema_version=2)]
        ).freeze()
        serialized = _continuation().to_dict()
        with self.assertRaisesRegex(SemanticChoiceError, "version changed"):
            registry.decode_continuation(serialized)


if __name__ == "__main__":
    unittest.main()
