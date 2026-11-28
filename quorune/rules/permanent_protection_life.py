from __future__ import annotations

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .fixed_controller_effect_shapes import fixed_life_node_capabilities
from .fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
from .node_capability_shapes import fixed_target_characteristics_node_capabilities


KEYWORDS = ("Hexproof", "Indestructible")
PROTECTION_MECHANICS = ("cr-611-continuous-effects", "hexproof", "indestructible")
TARGET_SCHEMA = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}


def target_protection_effects():
    return tuple({"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": keyword}
                 for keyword in KEYWORDS)


def controlled_protection_effect():
    return {"op": "modify_all_matching_permanents_until_end_of_turn",
            "predicate": ObjectQuerySpec(zones=("battlefield",), controller="$controller").to_dict(),
            "power": 0, "toughness": 0, "keywords": list(KEYWORDS)}


def protection_then_life_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) not in {2, 3}:
        return ()
    protection, life = effects[:-1], effects[-1]
    if life.get("op") != "life" or life.get("player") != "$controller":
        return ()
    if thaw_value(protection) == list(target_protection_effects()) and thaw_value(target_schema) == TARGET_SCHEMA:
        protect = fixed_target_characteristics_node_capabilities(
            effects=protection, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    elif thaw_value(protection) == [controlled_protection_effect()] and target_schema is None:
        protect = fixed_resolution_characteristic_set_node_capabilities(
            effects=protection, target_schema=None, mechanic_ids=mechanic_ids,
        )
    else:
        return ()
    gain = fixed_life_node_capabilities(effects=(life,), target_schema=None, mechanic_ids=mechanic_ids)
    return tuple(sorted({*protect, *gain})) if protect and gain else ()
