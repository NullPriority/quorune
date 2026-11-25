from __future__ import annotations

"""Choose the order of mandatory spell copies with distinct, fixed receivers."""

from ..replacement.immutable import FrozenMap
from ..rules.cast_creature_set_spell_copy import CAST_CREATURE_SET_COPY_FIELD
from ..semantic_runtime.intents import CopyStackItemIntent
from .model import (
    AutoContinue, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _receiver_plans(effect, actor, query):
    bundle = query.stack_target_schema(effect["stack"], actor=actor)
    return bundle["copy_plans"] if bundle is not None else {}


def _receiver_ids(plans):
    return FrozenMap({ref: plan["logical_object_id"] for ref, plan in plans.items()})


def _copy_intents(effect, order, plans, query):
    target = query.stack_object(effect["stack"])
    if target is None or target.copy_source is None:
        raise SemanticChoiceError("The copies require the original cast spell")
    actor = effect["_choice_actor"]
    return tuple(CopyStackItemIntent(
        actor=actor, controller=actor, target_stack_ref=target.ref,
        targets=plans[ref]["targets"], target_groups=FrozenMap(plans[ref]["groups"]),
        target_snapshots=FrozenMap(plans[ref]["snapshots"]), reason=effect["_stack_label"],
        copy_source=target.copy_source,
    ) for ref in order)


def prepare_cast_creature_set_copy(effect, context):
    plans = _receiver_plans(effect, context.actor, context.query)
    durable = FrozenMap({
        **{key: effect[key] for key in ("op", "stack", "copy_source", "player", CAST_CREATURE_SET_COPY_FIELD)},
        "_choice_actor": context.actor, "_stack_label": context.stack_label,
        "_copy_receiver_ids": _receiver_ids(plans),
    })
    refs = tuple(plans)
    if len(refs) <= 1:
        return SemanticChoicePreparation(
            request=None, continuation_effect=durable,
            preparation_intents=_copy_intents(durable, refs, plans, context.query) if refs else (),
            auto_continue=AutoContinue("The copies need no placement choice"),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Order the copies from bottom to top of the stack.",
            choice=OrderingChoice(field_name="order", legal_refs=refs, visibility="public"),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], "target_stack": effect["stack"]}),
        ),
        continuation_effect=durable,
    )


def complete_cast_creature_set_copy(effect, response, query):
    plans = _receiver_plans(effect, effect["_choice_actor"], query)
    order = response.get("order")
    if (
        not isinstance(order, (tuple, list)) or any(type(ref) is not str for ref in order)
        or len(order) != len(plans) or set(order) != set(plans)
        or "targets" in response or _receiver_ids(plans) != effect["_copy_receiver_ids"]
    ):
        raise SemanticChoiceError("Order every eligible creature exactly once")
    return SemanticChoiceCompletion(intents=_copy_intents(effect, order, plans, query))
