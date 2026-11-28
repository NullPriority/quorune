from __future__ import annotations

"""One untargeted directional control batch over the live seated players."""

from ..continuous_effect_model import ContinuousEffectDuration
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key


CAPABILITY = "continuous.control.directional_nonland_rotation"
MECHANIC = "directional-nonland-control"
TEMPLATE = "directional-nonland-control-v1"
FIELD = "directional_control_version"
DIRECTIONS = ("left", "right")
REQUIREMENTS = (CAPABILITY, "continuous.control.fixed_resolution")


def directional_control_result(direction):
    return {"op": "gain_control_set", FIELD: 1, "direction": direction}


def directional_control_choice():
    return {
        "op": "choose_option", "player": "$controller", "prompt": "Choose left or right.",
        "options": [{"id": direction, "label": direction.title()} for direction in DIRECTIONS],
        "then_by_choice": {direction: [directional_control_result(direction)] for direction in DIRECTIONS},
    }


def validate_directional_control_result(effect):
    direction = effect.get("direction")
    if (type(effect.get(FIELD)) is not int or type(direction) is not str or direction not in DIRECTIONS
            or FrozenMap(effect) != FrozenMap(directional_control_result(direction))):
        raise ValueError("Directional control requires one chosen direction")
    return direction


def directional_control_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1
            or FrozenMap(effects[0]) != FrozenMap(directional_control_choice())):
        return ()
    for branch in effects[0]["then_by_choice"].values():
        validate_directional_control_result(branch[0])
    return REQUIREMENTS


def directional_control_runtime_dependencies(program):
    if (MECHANIC not in getattr(program, "coverage", ()) and CAPABILITY not in getattr(program, "capability_dependencies", ())
            and not contains_nested_key(program.effects, FIELD)):
        return ()
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    required = directional_control_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.event != "activate" or program.active_zone != "battlefield"
            or program.event_condition is not None or program.trigger_limit is not None
            or program.cost_schema is None or len(program.handlers) != 1):
        raise ValueError("Directional control requires its complete printed loyalty activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.builtin_semantic_key != program.key or ability.loyalty_delta != -6
            or ability.mana_ability or ability.target_schema is not None
            or FrozenMap(activated_ability_cost(ability)) != FrozenMap(program.cost_schema)):
        raise ValueError("Directional control lost its original six-loyalty price")
    return required


def is_closed_directional_control_program(program):
    try:
        required = directional_control_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def execute_directional_control(host, intent):
    from ..continuous_effect_state import matching_battlefield_objects
    from ..control_effects import ControlEffectError, gain_control_of_assignments

    item = next((item for item in host.state.stack if item.ref == intent.source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.kind != "activated_ability" or item.controller != intent.actor
            or item.context.get("currently_resolving") is not True
            or item.source_object_id != intent.source.object_id
            or item.context.get("source_logical_object_id") != intent.source.logical_object_id
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_directional_control_program(program)):
        raise ControlEffectError("Directional control requires the original resolving activation and source")
    seats = tuple(host.active_seats)
    if len(seats) < 2:
        return ()
    offset = 1 if intent.direction == "left" else -1
    receiving_from = {seat: seats[(index + offset) % len(seats)] for index, seat in enumerate(seats)}
    cards = matching_battlefield_objects(host, ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",)))
    groups = {seat: [] for seat in seats}
    for card in cards:
        if (card.controller in groups
                and (card.object_id, card.logical_object_id) != (intent.source.object_id, intent.source.logical_object_id)):
            groups[card.controller].append(card.ref)
    assignments = tuple((ref, recipient) for recipient in host.apnap_order()
                        for ref in groups[receiving_from[recipient]])
    return gain_control_of_assignments(
        host, actor=intent.actor, assignments=assignments, duration=ContinuousEffectDuration.ZONE_OBJECT,
        source=intent.source, reason=intent.reason,
    )
