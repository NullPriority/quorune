"""Damage from one committed placement on a permanent or player."""

from ..counter_placement_event_model import COUNTER_EVENT_CAPABILITY
from ..recipient_counter_occurrences import RECIPIENT_COUNTER_EVENT, RecipientCounterOccurrence
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


RECIPIENT_COUNTER_DAMAGE_FIELD = "recipient_counter_damage"
RECIPIENT_COUNTER_DAMAGE_CAPABILITY = "damage.counter_placement.single_recipient_total"
RECIPIENT_COUNTER_DAMAGE_MECHANIC = "recipient-counter-placement-damage"
RECIPIENT_COUNTER_VARIANT = "counter_placement_any_recipient"
RECIPIENT_COUNTER_TARGET_PREDICATE = "player_or_creature_or_planeswalker"


def recipient_counter_damage_condition():
    return {"all": [
        {"field": "placing_player", "op": "eq", "value": "$source.controller"},
        {"field": "amount", "op": "gte", "value": 1},
    ]}


def recipient_counter_damage_target_schema():
    return {
        "zones": ["player", "battlefield"], "categories": ["player", "permanent"], "count": 1,
        "predicate": RECIPIENT_COUNTER_TARGET_PREDICATE, "controller_relation": "opponent",
    }


def recipient_counter_damage_effect():
    return {"op": "damage", "target": "$target.0", "source": "$source",
            RECIPIENT_COUNTER_DAMAGE_FIELD: {"schema_version": 1}}


def recipient_counter_damage_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (RECIPIENT_COUNTER_DAMAGE_MECHANIC not in mechanic_ids or len(effects) != 1
            or FrozenMap(target_schema or {}) != FrozenMap(recipient_counter_damage_target_schema())
            or FrozenMap(effects[0]) != FrozenMap(recipient_counter_damage_effect())):
        return ()
    marker = effects[0][RECIPIENT_COUNTER_DAMAGE_FIELD]
    if type(marker["schema_version"]) is not int:
        return ()
    return (RECIPIENT_COUNTER_DAMAGE_CAPABILITY, COUNTER_EVENT_CAPABILITY, "damage.amount.positive",
            "target.public.player_or_damageable_permanent", "target.revalidate_resolution", "trigger.placement.apnap",
            "target.player.static_hexproof_shroud")


def recipient_counter_damage_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if RECIPIENT_COUNTER_DAMAGE_FIELD in effect)
    if (not marked and RECIPIENT_COUNTER_DAMAGE_CAPABILITY not in program.capability_dependencies
            and program.event != RECIPIENT_COUNTER_EVENT):
        return ()
    dependencies = recipient_counter_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not dependencies or len(marked) != 1 or (program.event, program.active_zone) != (RECIPIENT_COUNTER_EVENT, "battlefield")
            or program.handlers or program.cost_schema is not None
            or FrozenMap(program.event_condition or {}) != FrozenMap(recipient_counter_damage_condition())):
        raise ValueError("Counter damage requires its exact grouped recipient occurrence and target")
    return dependencies


def is_closed_recipient_counter_damage_program(program):
    try:
        required = recipient_counter_damage_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_recipient_counter_damage(host, item, effect):
    if RECIPIENT_COUNTER_DAMAGE_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_recipient_counter_damage_program(program)
            or FrozenMap(effect) != FrozenMap(program.effects[0]) or item.kind != "triggered_ability"
            or item.context.get("event") != RECIPIENT_COUNTER_EVENT or item.context.get("placing_player") != item.controller
            or len(item.targets) != 1 or item.context.get("currently_resolving") is not True):
        raise ValueError("Counter damage requires the original resolving trigger and its placing player")
    event_context = item.context.get("event_context", item.context)
    occurrence = RecipientCounterOccurrence(FrozenMap(event_context))
    if occurrence.context["placing_player"] != item.controller:
        raise ValueError("The counter total lost its original placing player")
    return {**{key: value for key, value in effect.items() if key != RECIPIENT_COUNTER_DAMAGE_FIELD},
            "amount": occurrence.context["amount"]}
