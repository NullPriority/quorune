from __future__ import annotations

from ..replacement.immutable import FrozenMap
from ..phasing_model import selected_counter_phase_out_effect
from ..rules.proliferate_phase_out import counter_phase_choice_receipts
from .model import (
    AutoContinue, ReferenceSetChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_CHOICE_FIELDS = {"_choice_actor", "_legal_refs", "_stack_label", "_source_ref"}


def _current_recipients(effect, actor, query):
    try:
        receipts = counter_phase_choice_receipts({
            key: value for key, value in effect.items() if key not in _CHOICE_FIELDS
        })
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    eligible = {}
    for receipt in receipts:
        row = query.object(receipt["ref"], zones=("battlefield",))
        if (row is not None and row.controller == actor and not row.phased_out
                and row.object_id == receipt["object_id"]
                and row.logical_object_id == receipt["logical_object_id"]):
            eligible[row.ref] = receipt
    return eligible


def prepare_counter_phase_out_choice(effect, context):
    eligible = _current_recipients(effect, context.actor, context.query)
    if not eligible:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("No permanent you control received a counter"))
    refs = tuple(eligible)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose any number of permanents that received a counter to phase out.",
            choice=ReferenceSetChoice(field_name="objects", legal_refs=refs, minimum=0, maximum=len(refs)),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "proliferate", "options": refs}),
        ),
        continuation_effect=FrozenMap({
            **dict(effect), "_choice_actor": context.actor, "_legal_refs": refs,
            "_stack_label": context.stack_label, "_source_ref": context.source_ref,
        }),
    )


def complete_counter_phase_out_choice(continuation, response, query):
    effect = continuation.effect
    actor = effect.get("_choice_actor")
    if actor != continuation.semantic_frame.controller:
        raise SemanticChoiceError("Counter phasing requires the original spell controller")
    eligible = _current_recipients(effect, actor, query)
    legal_refs = effect.get("_legal_refs", ())
    selected = response.get("objects", response.get("choices", ()))
    if (not isinstance(selected, (tuple, list)) or any(type(ref) is not str for ref in selected)
            or len(selected) != len(set(selected))
            or any(ref not in legal_refs or ref not in eligible for ref in selected)):
        raise SemanticChoiceError("Choose only offered permanents that received counters")
    if not selected:
        return SemanticChoiceCompletion()
    recipients = tuple(eligible[ref] for ref in legal_refs if ref in selected)
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(selected_counter_phase_out_effect(recipients)),))
