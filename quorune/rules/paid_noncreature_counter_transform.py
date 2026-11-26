"""Bind the paid spell occurrence and its ordered source counter and transform."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import thaw_value
from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE

CAPABILITY = "trigger.resolution.paid_noncreature_counter_transform"
MECHANIC = "fixed-paid-noncreature-counter-transform"
THRESHOLD_FIELD = "paid_cast_source_counter_threshold"
TEMPLATE = MECHANIC + "-v1"
COVERAGE = (CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition", SPELL_HISTORY_TRANSFORM_COVERAGE)
DEPENDENCIES = (
    CAPABILITY, "counter.producer.fixed_effect", "permanent.transform.face_change",
    "trigger.event.normalized_spell_cast", "trigger.placement.apnap",
)


def event_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"not": {"field": "types", "op": "contains_any", "value": ["creature"]}},
        {"field": "mana_spent_total", "op": "gte", "value": 4},
    ]}


def ordered_effects():
    return (
        {"op": "place_counters", "card": "$source.zone_object",
         "counter": "+1/+1", "amount": 1, "source": "$source"},
        {"op": "transform", "card": "$source.zone_object",
         "expected_transform_count": "$source.transform_count",
         THRESHOLD_FIELD: {"schema_version": 1, "counter": "+1/+1", "minimum": 3}},
    )


def paid_cast_transform_runtime_dependencies(program):
    marked = (MECHANIC in program.coverage or CAPABILITY in program.capability_dependencies
              or program.provenance.get("template_id") == TEMPLATE
              or any(THRESHOLD_FIELD in effect for effect in program.effects))
    if not marked:
        return ()
    if ((program.event, program.active_zone) != ("spell.cast", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None
            or program.handlers or program.trigger_limit is not None
            or not set((*COVERAGE, MECHANIC)).issubset(program.coverage)
            or thaw_value(program.event_condition) != event_condition()
            or [thaw_value(effect) for effect in program.effects] != list(ordered_effects())):
        raise ValueError("Paid noncreature transform requires its complete occurrence and ordered source result")
    return DEPENDENCIES


def recheck_committed_payment(context):
    spent = context.get("mana_spent_total")
    stack = context.get("stack")
    return (context.get("event") == "spell.cast" and type(stack) is str and bool(stack)
            and type(spent) is int and spent >= 4)


def paid_cast_source_threshold_holds(value, source):
    if (value != ordered_effects()[1][THRESHOLD_FIELD] or type(value["schema_version"]) is not int
            or type(value["minimum"]) is not int):
        raise ValueError("Paid-cast transform requires the exact current source counter threshold")
    return source.counters.get(value["counter"], 0) >= value["minimum"]
