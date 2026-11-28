"""Optional targeted life loss and counters on the same landfall source."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


VERSION = 9
CAPABILITY = "effect.choice.targeted_life_loss_source_counters"
MECHANICS = ("fixed-optional-effect-choice", "cr-115-targets", "cr-119-life", "cr-122-counters")
LAND_CONDITION = FrozenMap({"field": "controller", "op": "eq", "value": "$source.controller"})


def target_player_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}


def optional_loss_source_counters_effect(
    *, actor="$controller", player="$target.0", card="$source.zone_object", source="$source",
):
    return {"op": "offer_optional_effect", "schema_version": VERSION, "player": actor, "effects": [
        {"op": "lose_life", "player": player, "amount": 3},
        {"op": "place_counters", "card": card, "counter": "+1/+1", "amount": 3, "source": source},
    ]}


def uses_optional_loss_source_counters(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "offer_optional_effect"
            and effect.get("schema_version") == VERSION)


def _bound_ref(value):
    return type(value) is str and bool(value) and not value.startswith("$")


def validate_optional_loss_source_counters(effect, *, actor=None, active_seats=()):
    if not uses_optional_loss_source_counters(effect) or type(effect["schema_version"]) is not int:
        raise ValueError("The land-entry choice requires its exact version")
    value = thaw_value(effect)
    children = value.get("effects")
    if (not isinstance(children, list) or len(children) != 2
            or any(not isinstance(child, dict) or type(child.get("amount")) is not int for child in children)):
        raise ValueError("The optional life and counter effects must remain in order")
    if actor is None:
        if value != optional_loss_source_counters_effect():
            raise ValueError("The land-entry choice requires its printed life loss and self counters")
        return
    target, card, source = children[0].get("player"), children[1].get("card"), children[1].get("source")
    if (actor not in active_seats or target not in active_seats or not _bound_ref(source)
            or (card is not None and card != source)
            or value != optional_loss_source_counters_effect(actor=actor, player=target, card=card, source=source)):
        raise ValueError("The optional life and counter choice lost its original roles")
    return tuple(FrozenMap(child) for child in children)


def optional_loss_source_counter_node_capabilities(*, effects, target_schema):
    if (len(effects) != 1 or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int or thaw_value(target_schema) != target_player_schema()):
        return ()
    try:
        validate_optional_loss_source_counters(effects[0])
    except ValueError:
        return ()
    return (
        CAPABILITY, "effect.choice.optional_fixed", "life.change.effect", "counter.producer.fixed_effect",
        "counter.placement.quantity_replacement", "target.revalidate_resolution",
        "trigger.event.normalized_zone_change", "trigger.placement.apnap",
    )


def optional_loss_source_counter_runtime_dependencies(program):
    if not any(uses_optional_loss_source_counters(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = optional_loss_source_counter_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("land.enter", "battlefield")
            or LAND_CONDITION != program.event_condition or program.handlers or program.cost_schema is not None
            or program.trigger_limit is not None):
        raise ValueError("The optional life and source counter sequence requires its complete controlled land-entry trigger")
    return required
