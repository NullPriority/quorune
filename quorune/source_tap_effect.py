from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .next_turn_effect import NEXT_TURN_EFFECT_FIELD


SOURCE_TAP_EFFECT_FIELD = "while_source_remains_tapped"
SOURCE_TAP_EFFECT_CAPABILITY = "continuous.resolution.while_source_remains_tapped"
SOURCE_TAP_UNTAP_CAPABILITY = "untap.step.while_source_remains_tapped"
SOURCE_TAP_MODIFIER_VERSION = 13
RUNTIME_SOURCE_CONTINUITY_FIELD = "_runtime_source_continuity"


def split_source_tap_effect(effect: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    if SOURCE_TAP_EFFECT_FIELD not in effect:
        return effect, False
    version = effect.get("schema_version")
    if (
        effect[SOURCE_TAP_EFFECT_FIELD] is not True
        or effect.get("op") != "apply_source_characteristics_until_end_of_turn"
        or type(version) is not int
        or version not in {2, 6, SOURCE_TAP_MODIFIER_VERSION}
        or NEXT_TURN_EFFECT_FIELD in effect
        or version == 6 and (effect.get("maximum_targets") != 1 or "announced_target_count" in effect)
    ):
        raise ValueError("Tapped-source duration requires one supported fixed characteristic result")
    return {key: value for key, value in effect.items() if key != SOURCE_TAP_EFFECT_FIELD}, True


def split_source_tap_untap_restraint(effect: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    if SOURCE_TAP_EFFECT_FIELD not in effect:
        return effect, False
    operation = effect.get("op")
    if (
        effect[SOURCE_TAP_EFFECT_FIELD] is not True
        or operation not in ("tap", "set_public_tap_state")
        or (operation == "set_public_tap_state" and effect.get("tapped") is not True)
    ):
        raise ValueError("A tapped-source untap restraint requires a supported tap instruction")
    return {key: value for key, value in effect.items() if key != SOURCE_TAP_EFFECT_FIELD}, True


def source_tap_effect_dependencies(value: Any) -> tuple[str, ...]:
    required = set()
    if isinstance(value, Mapping):
        if SOURCE_TAP_EFFECT_FIELD in value:
            if value.get("op") in ("tap", "set_public_tap_state"):
                split_source_tap_untap_restraint(value)
                required.add(SOURCE_TAP_UNTAP_CAPABILITY)
            else:
                split_source_tap_effect(value)
                required.add(SOURCE_TAP_EFFECT_CAPABILITY)
        children = value.values()
    elif isinstance(value, (list, tuple)):
        children = value
    else:
        return ()
    # Check every child even when an earlier one requires the capability.
    for child in children:
        required.update(source_tap_effect_dependencies(child))
    return tuple(sorted(required))
