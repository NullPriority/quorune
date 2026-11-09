from __future__ import annotations

"""Fixed live land-creature settings that explicitly retain land types."""

import re

from ..object_predicate import ObjectQuerySpec
from .continuous_templates import (
    _attached_ability_capabilities,
    _attached_modifier,
    _attached_type_values,
    _grant_attached_abilities,
)


_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_BASIC_LAND_SUBTYPES = {
    "plains": "plains",
    "islands": "island",
    "swamps": "swamp",
    "mountains": "mountain",
    "forests": "forest",
}
_ATTACHED_LAND_ANIMATION = re.compile(
    r"Enchanted land is (?:a|an) (?P<power>\d+)/(?P<toughness>\d+) "
    r"(?P<color>white |blue |black |red |green |colorless )?"
    r"(?:(?P<subtypes>[A-Za-z]+(?: [A-Za-z]+)*) )?creature"
    r"(?: with (?P<abilities>[^.]+?))?(?:\. It['’]s| that['’]s) still a land\.",
    re.IGNORECASE,
)


def _land_animation_modifier(match: re.Match[str]):
    modifier = _attached_modifier()
    modifier["type_operations"] = [{"op": "add_types", "field": "card_types", "values": ["Creature"]}]
    modifier["base_power"] = int(match["power"])
    modifier["base_toughness"] = int(match["toughness"])
    color = (match["color"] or "").strip().casefold()
    if color == "colorless":
        modifier["color_operations"] = [{"op": "remove_all_colors"}]
    elif color:
        modifier["color_operations"] = [{"op": "set_colors", "values": [_COLORS[color]]}]
    return modifier


def _animation_result(template_id, target, modifier):
    from ..semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler

    descriptor = {
        "handler_id": "continuous.characteristics.fixed-public-setting.v1",
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "target": target,
        "modifier": modifier,
    }
    FixedCharacteristicSettingsHandler().validate(descriptor)
    capabilities = {"continuous.characteristics.fixed_public_setting",
                    *_attached_ability_capabilities(modifier["add_abilities"])}
    return template_id, descriptor, tuple(sorted(capabilities))


def _attached_land_animation(match: re.Match[str]):
    modifier = _land_animation_modifier(match)
    if match["subtypes"]:
        subtypes = _attached_type_values(match["subtypes"])
        if subtypes is None:
            return None
        modifier["type_operations"].append({
            "op": "add_types", "field": "subtypes", "values": list(subtypes),
        })
    if match["abilities"] and not _grant_attached_abilities(modifier, match["abilities"]):
        return None
    target = {
        "kind": "attached",
        "controller": None,
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("land",)).to_dict(),
        "exclude_source": False,
    }
    return _animation_result("static-fixed-attached-land-animation-v1", target, modifier)


def static_land_animation_handler(text):
    if attached := _ATTACHED_LAND_ANIMATION.fullmatch(text):
        return _attached_land_animation(attached)
    match = re.fullmatch(
        r"All (?P<subject>lands|Plains|Islands|Swamps|Mountains|Forests) are (?P<power>\d+)/(?P<toughness>\d+) "
        r"(?P<color>white |blue |black |red |green |colorless )?creatures that are still lands\.",
        text, re.IGNORECASE,
    )
    if match is None:
        return None
    subtype = _BASIC_LAND_SUBTYPES.get(match["subject"].casefold())
    predicate = ObjectQuerySpec(zones=("battlefield",), types_all=("land",), subtypes_all=(subtype,) if subtype else ())
    target = {"kind": "fixed_query", "controller": "any", "predicate": predicate.to_dict(), "exclude_source": False}
    return _animation_result("static-fixed-land-animation-v1", target, _land_animation_modifier(match))
