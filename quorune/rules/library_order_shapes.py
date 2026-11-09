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
    is_affected_hand_top_choice,
    validate_fixed_shuffle_effect,
    validate_graveyard_library_order_effect,
)
from ..replacement.immutable import FrozenMap, thaw_value
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
        if effect.get("op") == GRAVEYARD_LIBRARY_ORDER_OPERATION and GRAVEYARD_LIBRARY_ORDER_MECHANIC in mechanic_ids:
            return _graveyard_library_order_capabilities(effect, target_schema)
        if is_affected_hand_top_choice(effect):
            from .affected_player_discard_capability_shapes import fixed_affected_player_hand_top_node_capabilities

            return fixed_affected_player_hand_top_node_capabilities(
                effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
            )
        if target_schema is not None:
            return ()
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
        validate_fixed_shuffle_effect(value)
        if value['player'] != '$controller':
            raise ValueError('Shuffle program requires reserved controller')
        return (SHUFFLE_CAPABILITY,)
    return ()


def is_closed_fixed_library_order_program(program):
    """Prove the leaf shape before provisional programs are promoted."""
    required = fixed_library_order_capabilities(
        effects=program.effects,
        target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
