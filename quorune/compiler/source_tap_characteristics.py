from __future__ import annotations

import re

from ..object_predicate import ObjectQuerySpec
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION, PERMANENT_CHARACTERISTIC_MECHANIC
from ..source_tap_effect import SOURCE_TAP_EFFECT_FIELD, SOURCE_TAP_MODIFIER_VERSION, split_source_tap_effect
from .target_characteristic_sets import target_characteristic_set_template


_CREATURE_SET = re.compile(r"all creatures get (?P<p>[+-][0-9]+)/(?P<t>[+-][0-9]+)", re.IGNORECASE)


def tapped_source_characteristics_template(match, *, compile_atomic):
    body = match["body"]
    affected = _CREATURE_SET.fullmatch(body)
    if affected is not None and match["land"] is None:
        power, toughness = int(affected["p"]), int(affected["t"])
        if not (power or toughness):
            return None
        effect = {
            "op": CHARACTERISTIC_OPERATION,
            "schema_version": SOURCE_TAP_MODIFIER_VERSION,
            "characteristics": {"power": power, "toughness": toughness},
            "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
            SOURCE_TAP_EFFECT_FIELD: True,
        }
        return "fixed-source-tapped-creature-set-v1", (effect,), None, (
            "cr-611-continuous-effects", PERMANENT_CHARACTERISTIC_MECHANIC,
        )
    body = re.sub(r"\band has\b", "and gains", body, flags=re.IGNORECASE)
    body = re.sub(r"^(target .+?) other than this creature has ", r"Other \1 gains ", body, flags=re.IGNORECASE)
    body = re.sub(r"^(target .+?) has ", r"\1 gains ", body, flags=re.IGNORECASE)
    equivalent = body + " until end of turn." + (" It's still a land." if match["land"] else "")
    compiled = target_characteristic_set_template(equivalent, include_single=True) or compile_atomic(equivalent)
    template, effects, schema, mechanics = compiled
    if template is None or len(effects) != 1:
        return None
    effect = {**effects[0], SOURCE_TAP_EFFECT_FIELD: True}
    try:
        split_source_tap_effect(effect)
    except ValueError:
        return None
    return f"{template}-source-tapped", (effect,), schema, mechanics
