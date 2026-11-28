"""Use the committed mandatory discard to authorize damage after the written draw."""

from collections.abc import Mapping

from ..additional_cost_vocabulary import DISCARD_ONE_COST
from ..object_predicate import ObjectQuerySpec
from ..paid_discard_model import PAID_DISCARD_CONTEXT, PaidDiscardSnapshot
from ..replacement.immutable import FrozenMap
from ..zone_trigger_events import ZoneTransitionKind
from ..zone_transition_model import PUBLIC_ZONES
from .casting_additional_costs import fixed_zone_change_additional_cost
from .effect_shape_walkers import nested_effects


PAID_NONLAND_DAMAGE_FIELD = "paid_nonland_discard_damage"
PAID_NONLAND_DAMAGE_CAPABILITY = "resolution.effect.committed_nonland_discard_damage"
PAID_NONLAND_DAMAGE_MECHANIC = "committed-nonland-discard-damage"


def paid_fixed_single_discard(cost_schema):
    costs = cost_schema.get("additional_costs") if isinstance(cost_schema, Mapping) else None
    if not isinstance(costs, (tuple, list)) or len(costs) != 1 or set(cost_schema) != {"additional_costs"}:
        return False
    try:
        cost = fixed_zone_change_additional_cost(costs[0])
    except (TypeError, ValueError, KeyError):
        return False
    return bool(cost is not None and cost.schema_version == 1 and cost.operation == DISCARD_ONE_COST
                and cost.count == 1 and cost.count_parameter is None
                and cost.predicate == ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True))


def paid_nonland_damage_effects(draw_count, damage):
    if any(type(value) is not int or not 1 <= value <= 20 for value in (draw_count, damage)):
        raise ValueError("The linked mandatory draw and damage require positive fixed counts")
    return (
        {"op": "draw", "player": "$controller", "count": draw_count, "private": True},
        {"op": "damage_each_opponent", "source": "$source", "amount": damage,
         PAID_NONLAND_DAMAGE_FIELD: {"schema_version": 1}},
    )


def paid_nonland_damage_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 2 or target_schema is not None or PAID_NONLAND_DAMAGE_MECHANIC not in mechanic_ids
            or not isinstance(effects[1].get(PAID_NONLAND_DAMAGE_FIELD), Mapping)):
        return ()
    try:
        if (type(effects[1][PAID_NONLAND_DAMAGE_FIELD].get("schema_version")) is not int
                or FrozenMap({"effects": effects}) != FrozenMap({
                    "effects": paid_nonland_damage_effects(effects[0]["count"], effects[1]["amount"]),
                })):
            return ()
    except (TypeError, KeyError, ValueError):
        return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities

    damage = {key: value for key, value in effects[1].items() if key != PAID_NONLAND_DAMAGE_FIELD}
    established = fixed_damage_node_capabilities(effects=(damage,), target_schema=None, mechanic_ids=("cr-120-damage",))
    return (PAID_NONLAND_DAMAGE_CAPABILITY, "casting.additional_cost.zone_change.fixed_discard",
            "zone.draw.library_to_hand", "zone.change.destination_replacement", *established) if established else ()


def paid_nonland_damage_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if PAID_NONLAND_DAMAGE_FIELD in effect)
    if not marked and PAID_NONLAND_DAMAGE_CAPABILITY not in program.capability_dependencies:
        return ()
    required = paid_nonland_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or not paid_fixed_single_discard(program.cost_schema) or len(marked) != 1
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers):
        raise ValueError("Nonland discard damage requires its complete mandatory fixed-price spell")
    return required


def is_closed_paid_nonland_damage_program(program):
    try:
        required = paid_nonland_damage_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def paid_discard_cast_context(host, program, costs):
    if program is None or PAID_NONLAND_DAMAGE_CAPABILITY not in program.capability_dependencies:
        return {}
    if not is_closed_paid_nonland_damage_program(program):
        raise ValueError("The paid discard requires its original fixed-price program")
    discarded = [row for row in costs.deferred_events if row[7] is ZoneTransitionKind.DISCARD]
    if len(discarded) != 1:
        raise ValueError("The result requires exactly one committed discard cost")
    paid, origin, _, logical_id, effective, _, destination, _ = discarded[0]
    if origin != "hand" or paid.logical_object_id == logical_id or not paid.is_card_object:
        raise ValueError("The cost lost its actual discarded hand incarnation")
    public = destination in PUBLIC_ZONES and not paid.face_down
    if public or logical_id in (costs.paid_discard_reveals or ()):
        types, _, _ = host._type_parts(effective["type_line"])
        receipt = PaidDiscardSnapshot(logical_id, is_land="land" in types)
    else:
        receipt = PaidDiscardSnapshot(logical_id, is_land=None)
    return {PAID_DISCARD_CONTEXT: receipt.to_dict()}


def bind_paid_nonland_damage(host, item, effect):
    if PAID_NONLAND_DAMAGE_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_paid_nonland_damage_program(program) or item.kind not in {"spell", "spell_copy"}
            or item.targets or item.context.get("currently_resolving") is not True
            or FrozenMap(effect) != FrozenMap(program.effects[1])):
        raise ValueError("Discard-linked damage requires the original complete resolving spell or its copy")
    receipt = PaidDiscardSnapshot.from_dict(item.context.get(PAID_DISCARD_CONTEXT))
    if receipt.is_land is not False:
        return None
    return {key: value for key, value in effect.items() if key != PAID_NONLAND_DAMAGE_FIELD}
