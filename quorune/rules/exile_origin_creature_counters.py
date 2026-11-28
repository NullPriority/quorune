"""Independent controller cast and entry receipts for a fixed creature counter set."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..counter_placement_sets import controlled_counter_subjects
from ..replacement.immutable import FrozenMap
from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition


CAPABILITY = "trigger.event.controller_cast_or_entry_from_exile"
TEMPLATE = "fixed-controller-exile-cast-or-entry-counters-v1"
DEPENDENCIES = (
    CAPABILITY, "trigger.subscription.fixed_multi_event", "trigger.event.normalized_spell_cast",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap",
)
BODY = "put a +1/+1 counter on each creature you control."


def exile_origin_counter_condition():
    condition = {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "from", "op": "eq", "value": "exile"},
    ]}
    return fixed_event_subscription_condition((
        FixedEventSubscription("spell.cast", condition),
        FixedEventSubscription("permanent.enter", condition),
    ))


def exile_origin_counter_effect():
    return {"op": "place_counters_on_set", "source": "$source",
            "set": controlled_counter_subjects("creature").to_dict(), "counter": "+1/+1", "amount": 1}


def is_exile_origin_counter_result(template, effects):
    return (template == TEMPLATE and len(effects) == 1
            and FrozenMap(effects[0]) == FrozenMap(exile_origin_counter_effect()))


def exile_origin_counter_runtime_dependencies(program):
    if CAPABILITY not in program.capability_dependencies and TEMPLATE not in str(program.provenance.get("template_id", "")):
        if not (program.event == "spell.cast" and program.event_condition and FrozenMap(program.event_condition) == FrozenMap(exile_origin_counter_condition())):
            return ()
    if ((program.event, program.active_zone) != ("spell.cast", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(exile_origin_counter_condition())
            or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(exile_origin_counter_effect())
            or type(program.effects[0]["amount"]) is not int or program.handlers
            or program.target_schema is not None or program.cost_schema is not None or program.trigger_limit is not None
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, "fixed_multi_event_subscription"}.issubset(program.coverage)):
        raise ValueError("Exile-origin counter triggers require each original event and the controlled creature result")
    return DEPENDENCIES
