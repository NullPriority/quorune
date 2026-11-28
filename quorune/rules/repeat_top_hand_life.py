"""Closed reveal, hand transfer and linked life-loss iterations for one spell."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


REPEAT_TOP_HAND_LIFE_FIELD = "repeat_revealed_top_hand_life"
REPEAT_TOP_HAND_LIFE_CAPABILITY = "library.top.repeated_revealed_hand_life"
REPEAT_TOP_HAND_LIFE_MECHANIC = "repeat-revealed-top-hand-life"


def repeat_top_hand_life_effect(*, player="$controller", stage="start", stack_ref=None):
    from .library_selection_capability_shapes import revealed_top_hand_life_effect

    if (stage not in {"start", "iterate", "offer"} or (stage == "start" and stack_ref is not None)
            or (stage != "start" and (type(stack_ref) is not str or not stack_ref))):
        raise ValueError("A repeat stage requires its exact resolving stack reference")
    marker = {"schema_version": 1, "stage": stage}
    if stack_ref is not None:
        marker["stack_ref"] = stack_ref
    return {**revealed_top_hand_life_effect(player=player), REPEAT_TOP_HAND_LIFE_FIELD: marker}


def validate_repeat_top_hand_life(effect, *, player="$controller", stack_ref=None):
    marker = effect.get(REPEAT_TOP_HAND_LIFE_FIELD)
    if (not isinstance(marker, Mapping) or type(marker.get("schema_version")) is not int
            or type(effect.get("schema_version")) is not int or type(marker.get("stage")) is not str):
        raise ValueError("The repeated reveal and hand transfer has a closed typed recipe")
    stage = marker["stage"]
    if FrozenMap(effect) != FrozenMap(repeat_top_hand_life_effect(player=player, stage=stage, stack_ref=stack_ref)):
        raise ValueError("The repeated reveal and hand transfer lost its original recipe")
    return stage


def repeat_top_hand_life_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if REPEAT_TOP_HAND_LIFE_FIELD in effect)
    if not marked and REPEAT_TOP_HAND_LIFE_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(program.effects) != 1 or len(marked) != 1 or (program.event, program.active_zone) != ("resolve", "stack")
            or program.target_schema is not None or program.cost_schema is not None
            or program.event_condition is not None or program.handlers
            or REPEAT_TOP_HAND_LIFE_MECHANIC not in program.coverage
            or validate_repeat_top_hand_life(program.effects[0]) != "start"):
        raise ValueError("Repeated reveal requires the entire original untargeted spell")
    from .library_selection_capability_shapes import FIXED_LIBRARY_SELECTION_CAPABILITY

    return REPEAT_TOP_HAND_LIFE_CAPABILITY, FIXED_LIBRARY_SELECTION_CAPABILITY, "life.change.effect"


def is_closed_repeat_top_hand_life_program(program):
    try:
        required = repeat_top_hand_life_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_repeat_top_hand_life(host, item, effect):
    if REPEAT_TOP_HAND_LIFE_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (program is None or item.kind not in {"spell", "spell_copy"} or item.targets
            or item.context.get("currently_resolving") is not True
            or not host.semantic_program_is_current_trusted(program)
            or not is_closed_repeat_top_hand_life_program(program)):
        raise ValueError("The next reveal or repeat choice requires its original resolving spell")
    if FrozenMap(effect) == FrozenMap(program.effects[0]):
        return repeat_top_hand_life_effect(player=item.controller, stage="iterate", stack_ref=item.ref)
    validate_repeat_top_hand_life(effect, player=item.controller, stack_ref=item.ref)
    return effect
