"""Separate exact leaf proof for ordering and shuffle capabilities."""
from collections.abc import Mapping

from ..library_order_model import (
    FixedTopLibraryOrderSpec,
    FixedHandTopOrderSpec,
    GRAVEYARD_LIBRARY_ORDER_CAPABILITY,
    GRAVEYARD_LIBRARY_ORDER_MECHANIC,
    GRAVEYARD_LIBRARY_ORDER_OPERATION,
    HAND_TOP_ORDER_OPERATION,
    HAND_TOP_ORDER_CAPABILITY,
    HAND_TOP_ORDER_MECHANIC,
    TOP_ORDER_OPERATION,
    TOP_ORDER_CAPABILITY,
    TOP_ORDER_MECHANIC,
    SHUFFLE_OPERATION,
    SHUFFLE_CAPABILITY,
    SHUFFLE_MECHANIC,
    PAID_PRIVATE_TOP_CYCLE, is_affected_hand_top_choice, uses_paid_private_top_cycle,
    validate_paid_private_top_cycle,
    validate_fixed_shuffle_effect,
    validate_graveyard_library_order_effect,
)
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects

PAID_TOP_CYCLE_CAPABILITIES = (
    TOP_ORDER_CAPABILITY, SHUFFLE_CAPABILITY, "library.select.fixed_controller",
    FIXED_EFFECT_PAYMENT_CAPABILITY, "casting.additional_cost.fixed_life_payment",
)


def paid_private_top_cycle_runtime_dependencies(program):
    if not any(PAID_PRIVATE_TOP_CYCLE in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or program.target_schema is not None
            or program.event_condition is not None or program.cost_schema is not None
            or program.handlers or len(program.effects) != 1):
        raise ValueError("The life-paid top cycle must be the complete resolving spell")
    validate_paid_private_top_cycle(program.effects[0])
    return PAID_TOP_CYCLE_CAPABILITIES
from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality
from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


def _graveyard_library_order_capabilities(effect, target_schema):
    validate_graveyard_library_order_effect(effect)
    if not isinstance(target_schema, Mapping):
        return ()
    schema = thaw_value(FrozenMap(target_schema))
    if "count" in schema:
        return ()
    opponent = effect["scope"] == "opponent"
    if opponent and schema.pop("same_owner", None) is not True:
        return ()
    any_number = "count_parameter" in schema
    if any_number:
        count = AnnouncedTargetCardinality.from_dict(schema.pop("count_parameter"))
        if count.parameter != "any_number":
            return ()
    else:
        maximum = schema.pop("up_to", None)
        if type(maximum) is not int or not 2 <= maximum <= 6:
            return ()
    target = PublicGraveyardCardTargetSpec.from_target_schema({**schema, "count": 1})
    if target.owner_relation != ("opponent" if opponent else "you") or target.kind not in {
        PublicGraveyardCardTargetKind.CARD,
        PublicGraveyardCardTargetKind.ARTIFACT_CARD,
        PublicGraveyardCardTargetKind.CREATURE_CARD,
    }:
        return ()
    required = (GRAVEYARD_LIBRARY_ORDER_CAPABILITY, "target.revalidate_resolution")
    return (*required, ANY_NUMBER_TARGET_CAPABILITY) if any_number else required


def fixed_library_order_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping):
        return ()
    try:
        if 'target_library_shuffle_version' in effect:
            from .target_library_shuffle import target_shuffle_node_capabilities
            return target_shuffle_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
        if effect.get("op") == GRAVEYARD_LIBRARY_ORDER_OPERATION and GRAVEYARD_LIBRARY_ORDER_MECHANIC in mechanic_ids:
            return _graveyard_library_order_capabilities(effect, target_schema)
        if is_affected_hand_top_choice(effect):
            from .affected_player_discard_capability_shapes import fixed_affected_player_hand_top_node_capabilities

            return fixed_affected_player_hand_top_node_capabilities(
                effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
            )
        if target_schema is not None:
            return ()
        if uses_paid_private_top_cycle(effect):
            validate_paid_private_top_cycle(effect)
            return PAID_TOP_CYCLE_CAPABILITIES if {TOP_ORDER_MECHANIC, SHUFFLE_MECHANIC} <= set(mechanic_ids) else ()
        if effect.get('op') == TOP_ORDER_OPERATION and TOP_ORDER_MECHANIC in mechanic_ids:
            FixedTopLibraryOrderSpec.from_effect(effect)
            return (TOP_ORDER_CAPABILITY,) if effect['player'] == '$controller' else ()
        if effect.get("op") == HAND_TOP_ORDER_OPERATION and HAND_TOP_ORDER_MECHANIC in mechanic_ids:
            FixedHandTopOrderSpec.from_effect(effect)
            return (HAND_TOP_ORDER_CAPABILITY,) if effect["player"] == "$controller" else ()
        if effect.get('op') == SHUFFLE_OPERATION and SHUFFLE_MECHANIC in mechanic_ids:
            validate_fixed_shuffle_effect(effect)
            return (SHUFFLE_CAPABILITY,) if effect['player'] == '$controller' else ()
    except (TypeError, ValueError):
        return ()
    return ()


def library_order_payload_dependencies(value):
    if not isinstance(value, Mapping):
        return ()
    if value.get("op") == GRAVEYARD_LIBRARY_ORDER_OPERATION:
        validate_graveyard_library_order_effect(value)
        return (GRAVEYARD_LIBRARY_ORDER_CAPABILITY,)
    if uses_paid_private_top_cycle(value):
        validate_paid_private_top_cycle(value)
        return PAID_TOP_CYCLE_CAPABILITIES
    if value.get('op') == TOP_ORDER_OPERATION:
        FixedTopLibraryOrderSpec.from_effect(value)
        if value['player'] != '$controller':
            raise ValueError('Top order program requires reserved controller')
        return (TOP_ORDER_CAPABILITY,)
    if value.get("op") == HAND_TOP_ORDER_OPERATION:
        FixedHandTopOrderSpec.from_effect(value)
        if value["player"] != "$controller":
            raise ValueError("Hand-to-top order program requires reserved controller")
        return (HAND_TOP_ORDER_CAPABILITY,)
    if value.get('op') == SHUFFLE_OPERATION:
        if 'target_library_shuffle_version' in value:
            from .target_library_shuffle import validate_target_shuffle_effect, TARGET_SHUFFLE_CAPABILITY
            validate_target_shuffle_effect(value)
            return (TARGET_SHUFFLE_CAPABILITY,SHUFFLE_CAPABILITY,'target.revalidate_resolution')
        validate_fixed_shuffle_effect(value)
        if value['player'] != '$controller':
            raise ValueError('Shuffle program requires reserved controller')
        return (SHUFFLE_CAPABILITY,)
    return ()


def is_closed_fixed_library_order_program(program):
    """Prove the leaf shape before provisional programs are promoted."""
    if len(program.effects) == 1 and uses_paid_private_top_cycle(program.effects[0]):
        try:
            return set(paid_private_top_cycle_runtime_dependencies(program)) <= set(program.capability_dependencies)
        except (TypeError, ValueError, KeyError):
            return False
    required = fixed_library_order_capabilities(
        effects=program.effects,
        target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
