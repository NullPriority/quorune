from __future__ import annotations

"""Choose whether to create one copy with a rules-specified target."""

from ..replacement.immutable import FrozenMap
from ..rules.cast_source_spell_copy import source_cast_copy_effect
from ..semantic_runtime.intents import CopyStackItemIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def prepare_source_cast_copy(effect, context):
    target_ref = effect["stack"]
    schema = context.query.stack_target_schema(target_ref, actor=context.actor)
    if schema is None:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The indicated creature is unavailable as a copy target"),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Copy the spell targeting this creature?",
            choice=ScalarChoice(field_name="choice", legal_values=("apply", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], "target_stack": target_ref}),
        ),
        continuation_effect=FrozenMap({
            **{name: effect[name] for name in source_cast_copy_effect()},
            "_choice_actor": context.actor, "_stack_label": context.stack_label,
        }),
    )


def complete_source_cast_copy(effect, response, query):
    option = response.get("choice")
    if option not in ("apply", "decline") or "targets" in response:
        raise SemanticChoiceError("Choose apply or decline for the fixed copy")
    if option == "decline":
        return SemanticChoiceCompletion()
    target_ref = effect["stack"]
    actor = effect["_choice_actor"]
    schema = query.stack_target_schema(target_ref, actor=actor)
    target = query.stack_object(target_ref)
    if schema is None or target is None or target.copy_source is None:
        raise SemanticChoiceError("The indicated creature is unavailable as a copy target")
    targets, groups = query.validate_stack_targets(
        target_ref, None, actor=actor, target_schema=schema["authoritative"],
    )
    return SemanticChoiceCompletion(intents=(CopyStackItemIntent(
        actor=actor, controller=actor, target_stack_ref=target_ref,
        targets=targets, target_groups=FrozenMap(groups), reason=effect["_stack_label"],
        target_snapshots=FrozenMap(query.copied_target_snapshots(target_ref, actor=actor)),
        copy_source=target.copy_source,
    ),))
