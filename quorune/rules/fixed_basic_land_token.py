"""Closed tapped named land tokens with all five basic land subtypes."""

from collections.abc import Mapping
import re

from ..basic_land_types import BASIC_LAND_MANA
from ..fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD, fixed_token_effect_shape_is_closed
from ..replacement.immutable import thaw_value


FIXED_BASIC_LAND_TOKEN_CAPABILITY = "token.creation.named_all_basic_land_types"
FIXED_BASIC_LAND_TOKEN_MECHANIC = "fixed-named-all-basic-land-types-token"
_TYPE_LINE = "Token Land — " + " ".join(name.title() for name in BASIC_LAND_MANA)
_TOKEN_NAME = re.compile(r"[A-Z][A-Za-z']*(?:[ -](?:[A-Z][A-Za-z']*|the|of|and|to|a|an|in|on|at|for))*")


def fixed_basic_land_token_effect(name: str) -> dict[str, object]:
    if type(name) is not str or _TOKEN_NAME.fullmatch(name) is None:
        raise ValueError("A fixed land token needs its exact printed name")
    return {
        "op": "create_token", "controller": "$controller", "name": name, "quantity": 1,
        "tapped": True, "characteristics": {
            "type_line": _TYPE_LINE, "colors": [], TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
        },
    }


def fixed_basic_land_token_payload_dependencies(effect) -> tuple[str, ...]:
    if not isinstance(effect, Mapping) or effect.get("op") != "create_token":
        return ()
    characteristics = effect.get("characteristics")
    if not isinstance(characteristics, Mapping):
        return ()
    line = characteristics.get("type_line")
    if type(line) is not str or line.split(" — ", 1)[0] != "Token Land":
        return ()
    if (
        not fixed_token_effect_shape_is_closed(effect) or type(effect["quantity"]) is not int
        or type(characteristics.get(TOKEN_LITERAL_NAME_VERSION_FIELD)) is not int
        or thaw_value(effect) != fixed_basic_land_token_effect(effect["name"])
    ):
        raise ValueError("The named land token definition requires all basic land subtypes, no Basic supertype and tapped entry")
    return ("token.creation.fixed_definition", "mana.intrinsic.basic_land_type", FIXED_BASIC_LAND_TOKEN_CAPABILITY)


def fixed_basic_land_token_closure(effect):
    try:
        dependencies = fixed_basic_land_token_payload_dependencies(effect)
    except (TypeError, ValueError):
        return None
    if dependencies:
        return dependencies, {"cr-111-tokens", "fixed-named-token", FIXED_BASIC_LAND_TOKEN_MECHANIC}
    return None
