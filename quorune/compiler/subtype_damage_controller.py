"""Match printed damage from a subtype and its controller's fixed result."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap
from ..rules.subtype_damage_controller import (
    LIFE_VARIANT, MECHANIC, TOKEN_VARIANT, subtype_damage_condition, subtype_damage_effect,
)

_PATTERN = re.compile(
    r"Whenever a (?P<subtype>.+?) deals (?P<combat>combat damage to a player|damage), (?P<body>.+)", re.I,
)


def subtype_damage_binding_parts(text):
    match = _PATTERN.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    subtype = canonical_creature_subtype(match["subtype"])
    if subtype is None:
        return None
    combat_token = match["combat"].casefold() != "damage"
    body = match["body"]
    expected = (f"its controller may create a 1/1 colorless {subtype} creature token."
                if combat_token else "its controller gains that much life.")
    if body.casefold() != expected:
        return None
    return (TOKEN_VARIANT if combat_token else LIFE_VARIANT), body, subtype_damage_condition(
        subtype, combat_token=combat_token,
    )


def subtype_damage_result(binding, body):
    if binding.variant not in {TOKEN_VARIANT, LIFE_VARIANT} or body != binding.body:
        return None
    if binding.event.value != "damage.dealt" or binding.active_zone != "battlefield":
        return None
    combat_token = binding.variant == TOKEN_VARIANT
    condition = binding.event_condition
    source_filter = condition["all"][0] if combat_token else condition
    subtype, = source_filter["value"]
    if FrozenMap(condition) != FrozenMap(subtype_damage_condition(subtype, combat_token=combat_token)):
        return None
    try:
        effect = subtype_damage_effect(subtype, combat_token=combat_token)
    except ValueError:
        return None
    return (("subtype-damage-controller-token-v1" if combat_token else "subtype-damage-controller-life-v1",
             (effect,), None, (MECHANIC,)), False)
