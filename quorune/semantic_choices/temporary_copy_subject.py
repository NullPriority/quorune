"""Offer the resolving controller one public, untargeted creature model."""

from dataclasses import dataclass
from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..rules.temporary_copy_subject import (
    TEMPORARY_COPY_SUBJECT_OPERATION, TEMPORARY_COPY_SUBJECT_RULES,
    TemporaryCopySubjectScope, copy_subject_commit_effect, copy_subject_object_reference,
    decode_temporary_copy_subject_effect,
)
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _retained_receivers(query, refs, actor, scope):
    receivers = []
    for ref in refs:
        row = query.object(ref, zones=("battlefield",))
        if (row is None or row.phased_out or "creature" not in row.types
                or scope.multiple_receivers and row.controller != actor):
            raise SemanticChoiceError("A declared copy receiver is no longer eligible")
        receivers.append(copy_subject_object_reference(row))
    return tuple(receivers)


@dataclass(frozen=True, slots=True)
class TemporaryCopySubjectChoiceHandler:
    operation: str = TEMPORARY_COPY_SUBJECT_OPERATION
    handler_id: str = "choice.copy.temporary-public-subject.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = TEMPORARY_COPY_SUBJECT_RULES
    capability_dependencies: tuple[str, ...] = ("target.revalidate_resolution",)
    continuation_fields: tuple[str, ...] = ("scope", "actor", "receivers", "offered")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("SemanticChoiceCompletion.prepend_effects", "copy_until_end_of_turn")
    replay_fixture: str = "temporary-public-copy-subject"
    test_modules: tuple[str, ...] = ("tests.test_temporary_copy_subject",)

    def prepare(self, effect, context):
        from ..rules.linked_clue_copy import has_linked_clue_copy_version
        if has_linked_clue_copy_version(effect):
            from .linked_clue_copy import prepare_linked_clue_copy

            return prepare_linked_clue_copy(effect, context)
        try:
            actor = context.actor
            if actor != context.stack_controller:
                raise ValueError("Temporary copy choice requires its original resolving controller")
            scope = decode_temporary_copy_subject_effect(effect, actor=actor)
            receivers = _retained_receivers(context.query, effect["receivers"], actor, scope)
            rows = sorted(context.query.objects(zones=("battlefield",)), key=lambda row: row.ref)
            offered = {row.ref: copy_subject_object_reference(row) for row in rows if scope.accepts_subject(row)}
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        saved = FrozenMap({"op": self.operation, "scope": scope.value, "actor": actor,
                           "receivers": receivers, "offered": offered})
        if not offered:
            return SemanticChoicePreparation(request=None, continuation_effect=saved,
                auto_continue=AutoContinue(reason="No eligible creature can supply the copy"))
        prompt = ("Choose a creature on the battlefield to copy." if scope.multiple_receivers
                  else "Choose a nonlegendary creature on the battlefield to copy.")
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt, choice=ObjectChoice(field_name="card", legal_refs=tuple(offered), zones=("battlefield",)),
                public_context=FrozenMap({"operation": self.operation, "stack": context.stack_ref}),
            ),
            continuation_effect=saved,
        )

    def complete(self, continuation, response, query):
        from ..rules.linked_clue_copy import has_linked_clue_copy_version
        effect = continuation.effect
        if has_linked_clue_copy_version(effect):
            from .linked_clue_copy import complete_linked_clue_copy

            return complete_linked_clue_copy(continuation, response, query)
        if (set(effect) != {"op", "scope", "actor", "receivers", "offered"} or effect["op"] != self.operation
                or effect["actor"] != continuation.semantic_frame.controller
                or not isinstance(effect["offered"], Mapping) or not isinstance(effect["receivers"], tuple)):
            raise SemanticChoiceError("Temporary copy subject lost its original choice and targets")
        if set(response) - {"card", "action", "action_id", "choice_schema", "reason"}:
            raise SemanticChoiceError("Temporary copy subject accepts one offered creature reference")
        try:
            scope = TemporaryCopySubjectScope(effect["scope"])
            ref = response.get("card")
            if type(ref) is not str or ref not in effect["offered"]:
                raise ValueError("Choose an offered copy subject")
            current = query.object(ref, zones=("battlefield",))
            if not scope.accepts_subject(current) or effect["offered"][ref] != FrozenMap(copy_subject_object_reference(current)):
                raise ValueError("The chosen copy subject changed before completion")
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(prepend_effects=(
            FrozenMap(copy_subject_commit_effect(scope, effect["offered"][ref], effect["receivers"])),
        ))
