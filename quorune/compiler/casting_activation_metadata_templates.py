from __future__ import annotations

"""Closed Oracle grammar for casting and activation runtime metadata."""

import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..semantic_runtime.casting_activation_metadata import (
    LOYALTY_COST_MODIFIER_EVENT,
    LOYALTY_COST_MODIFIER_HANDLER_ID,
    SELF_ZONE_CAST_PERMISSION_EVENT,
    SELF_ZONE_CAST_PERMISSION_HANDLER_ID,
    SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID,
    SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY,
)


CastingActivationMetadataTemplate = tuple[str, Mapping[str, Any], str | tuple[str, ...]]


_SELF_GRAVEYARD_CONTROLLED_SUBTYPE = re.compile(
    r"^You may cast this card from your graveyard as long as you control "
    r"(?:a|an) (?P<subtype>[A-Za-z][A-Za-z' -]*)\.?$",
    re.IGNORECASE,
)
_OPPONENT_PLANESWALKER_LOYALTY_MANA_INCREASE = re.compile(
    r"^Loyalty abilities of planeswalkers your opponents control cost "
    r"\{(?P<amount>[1-9][0-9]*)\} more to activate\.?$",
    re.IGNORECASE,
)
_CONTROLLER_LOYALTY_INCREASE = re.compile(
    r"^Planeswalkers' loyalty abilities you activate cost an additional "
    r"\[\+(?P<amount>[1-9][0-9]*)\] to activate\.?$",
    re.IGNORECASE,
)


def static_self_zone_cast_permission_handler(
    text: str,
    *, source_name: str = 'source',
) -> CastingActivationMetadataTemplate | None:
    """Lower explicit self-graveyard permission behind a closed public condition."""

    simple = re.fullmatch(r'You may cast this card from (?P<first>your graveyard|exile)(?: or from (?P<second>your graveyard|exile))?\.?', text.strip(), re.I)
    if simple is not None:
        from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
        zones = [zone.casefold().removeprefix('your ') for zone in (simple['first'], simple['second']) if zone]
        if len(zones) != len(set(zones)):
            return None
        return 'self-public-zone-cast-v1', {'handler_id': SELF_PUBLIC_ZONE_CAST_PERMISSION_HANDLER_ID,
            'schema_version': 1, 'event': SELF_ZONE_CAST_PERMISSION_EVENT, 'source_zones': sorted(zones),
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}, SELF_PUBLIC_ZONE_CAST_PERMISSION_CAPABILITY
    match = _SELF_GRAVEYARD_CONTROLLED_SUBTYPE.fullmatch(text.strip())
    subtype = canonical_creature_subtype(match.group("subtype")) if match is not None else None
    if subtype is None:
        from .public_state_queries import fixed_public_state_condition
        from .player_status_conditions import max_speed_condition_parts
        speed = max_speed_condition_parts(text.strip())
        if speed is not None:
            condition,body=speed
            if body.casefold()!='you may cast this card from your graveyard.':
                return None
            return _public_self_zone_permission(condition)
        conditional = re.fullmatch(r'You may cast this card from your graveyard (?:as long as|if) (?P<condition>.+?)\.?', text.strip(), re.I)
        if conditional is None:
            return None
        condition = fixed_public_state_condition(conditional['condition'], source_name=source_name)
        if condition is None:
            return None
        return _public_self_zone_permission(condition)
    return (
        "self-graveyard-cast-controlled-subtype-v1",
        {
            "handler_id": SELF_ZONE_CAST_PERMISSION_HANDLER_ID,
            "schema_version": 1,
            "event": SELF_ZONE_CAST_PERMISSION_EVENT,
            "source_zone": "graveyard",
            "controlled_subtypes_any": [subtype],
        },
        "casting.zone.self_graveyard.controlled_subtype",
    )


def _public_self_zone_permission(condition):
    capabilities={'casting.zone.self_graveyard.controlled_subtype','continuous.characteristics.fixed_public_state'}
    if condition.kind.value=='controller_max_speed':capabilities.add('variant.speed.player')
    elif condition.kind.value=='controller_is_monarch':capabilities.add('variant.monarch.designate')
    return 'self-graveyard-cast-public-state-v1', {
        'handler_id':'permission.cast.self-zone-public-state.v1','schema_version':2,
        'event':SELF_ZONE_CAST_PERMISSION_EVENT,'source_zone':'graveyard',
        'source_condition':condition.to_dict(),
    }, tuple(sorted(capabilities))


def static_loyalty_cost_modifier_handler(
    text: str,
) -> CastingActivationMetadataTemplate | None:
    """Lower the two represented public loyalty-cost modifier sentences."""

    normalized = text.strip()
    match = _OPPONENT_PLANESWALKER_LOYALTY_MANA_INCREASE.fullmatch(normalized)
    if match is not None:
        affected_controller = "opponent"
        adjustment_kind = "generic_mana_increase"
    else:
        match = _CONTROLLER_LOYALTY_INCREASE.fullmatch(normalized)
        if match is None:
            return None
        affected_controller = "source_controller"
        adjustment_kind = "loyalty_increase"
    return (
        "loyalty-cost-modifier-marker-v1",
        {
            "handler_id": LOYALTY_COST_MODIFIER_HANDLER_ID,
            "schema_version": 1,
            "event": LOYALTY_COST_MODIFIER_EVENT,
            "affected_controller": affected_controller,
            "adjustment_kind": adjustment_kind,
            "amount": int(match.group("amount")),
        },
        "activation.loyalty_cost.modifier_detection",
    )


__all__ = [
    "CastingActivationMetadataTemplate",
    "static_loyalty_cost_modifier_handler",
    "static_self_zone_cast_permission_handler",
]
