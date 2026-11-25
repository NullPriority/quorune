"""The ordinary counter intent with a sealed reflexive consequence."""

from dataclasses import dataclass
from typing import Mapping

from ..reflexive_trigger_model import SOURCE_COUNTER_REFLEXIVE_FIELD
from ..replacement.immutable import FrozenMap
from ..rules.source_counter_reflexive import source_counter_reflexive_child
from .context import SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import IntentPlan, PlaceCountersIntent


@dataclass(frozen=True, slots=True)
class PlaceSourceCounterReflexiveIntent(PlaceCountersIntent):
    parent_stack_ref: str = ""
    child_program: FrozenMap | None = None

    def __post_init__(self):
        PlaceCountersIntent.__post_init__(self)
        if (type(self.parent_stack_ref) is not str or not self.parent_stack_ref
                or not isinstance(self.child_program, Mapping)
                or self.counter_name != "+1/+1" or type(self.amount) is not int or self.amount != 1
                or self.effect_generated is not True or len(self.object_refs) != 1):
            raise ValueError("The reflexive result requires one ordinary realized source counter")
        child, _ = source_counter_reflexive_child(self.child_program)
        object.__setattr__(self, "child_program", FrozenMap(child.to_dict()))


def lower_source_counter_reflexive(effect, context, *, handler_id):
    try:
        child, _ = source_counter_reflexive_child(effect[SOURCE_COUNTER_REFLEXIVE_FIELD])
        if (context.source is None or effect.get("counter") != "+1/+1"
                or type(effect.get("amount")) is not int
                or effect["amount"] != 1):
            raise ValueError("The counter reflexive instruction requires its original one-counter source")
        if effect.get("card") is None:
            return IntentPlan(operation="place_counters", handler_id=handler_id, intents=())
        if context.source.object_id is None:
            raise ValueError("A live counter recipient requires the original physical source")
        fields = validate_direct_target_effect(
            effect, context, operation="place_counters", reference_field="card",
            family_label="Source counter reflexive", allow_replacement_selections=True,
            additional_allowed_fields=("counter", "amount", "source", SOURCE_COUNTER_REFLEXIVE_FIELD),
        )
        intent = PlaceSourceCounterReflexiveIntent(
            actor=context.actor, object_refs=(fields.object_ref,), counter_name="+1/+1", amount=1,
            reason=fields.reason, source_ref=effect.get("source"), replacement_selections=fields.replacement_selections,
            parent_stack_ref=context.source.stack_ref, child_program=FrozenMap(child.to_dict()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return IntentPlan(operation="place_counters", handler_id=handler_id, intents=(intent,))
