"""Apply or decline a prepared occurrence-bound copy without changing its model."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.temporary_occurrence_copy import (
    TEMPORARY_OCCURRENCE_COPY_OPERATION, TEMPORARY_OCCURRENCE_COPY_RULES,
    temporary_occurrence_copy_commit_effect, validate_bound_temporary_occurrence_copy,
)
from ..rules.temporary_copy_subject import copy_subject_object_reference
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _current_receiver(effect, query):
    ref = effect["receiver"].get("ref")
    if type(ref) is not str or not ref:
        raise SemanticChoiceError("Temporary occurrence copy lost its original receiver reference")
    row = query.object(ref, zones=("battlefield",))
    return row is not None and not row.phased_out and FrozenMap(copy_subject_object_reference(row)) == effect["receiver"]


@dataclass(frozen=True, slots=True)
class TemporaryOccurrenceCopyChoiceHandler:
    operation: str = TEMPORARY_OCCURRENCE_COPY_OPERATION
    handler_id: str = "choice.copy.temporary-occurrence.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = TEMPORARY_OCCURRENCE_COPY_RULES
    capability_dependencies: tuple[str, ...] = ("effect.choice.optional_fixed",)
    continuation_fields: tuple[str, ...] = ("player", "receiver", "model_ref", "values")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("SemanticChoiceCompletion.prepend_effects", "copy_until_end_of_turn")
    replay_fixture: str = "temporary-occurrence-copy-choice"
    test_modules: tuple[str, ...] = ("tests.test_temporary_occurrence_copy",)

    def prepare(self, effect, context):
        try:
            if context.actor != context.stack_controller:
                raise ValueError("Temporary occurrence copy uses its original ability controller")
            validate_bound_temporary_occurrence_copy(effect, context.actor)
            retained = FrozenMap(effect)
            if not _current_receiver(retained, context.query):
                return SemanticChoicePreparation(request=None, continuation_effect=retained,
                    auto_continue=AutoContinue(reason="The original copy receiver is unavailable"))
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Become a copy of the creature that entered?",
                choice=ScalarChoice(field_name="choice", legal_values=("apply", "decline")),
                public_context=FrozenMap({"operation": self.operation, "stack": context.stack_ref}),
            ),
            continuation_effect=retained,
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        try:
            validate_bound_temporary_occurrence_copy(effect, continuation.semantic_frame.controller)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        choice = response.get("choice")
        if type(choice) is not str or choice not in ("apply", "decline"):
            raise SemanticChoiceError("Choose apply or decline")
        if choice == "decline" or not _current_receiver(effect, query):
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(prepend_effects=(temporary_occurrence_copy_commit_effect(effect),))
