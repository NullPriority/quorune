from __future__ import annotations

"""Fixed reductions for spells matching the source's chosen creature type."""

import re

from ..cast_cost_modifiers import (
    CHOSEN_TYPE_CAST_COST_CAPABILITY, CHOSEN_TYPE_CAST_COST_HANDLER_ID,
    CastCostAffectedController, PublicCastCostModifierV2Spec,
)
from ..object_predicate import ObjectQuerySpec
from .public_cast_cost_modifiers import _mana_adjustment


CHOSEN_TYPE_CAST_COST_TEMPLATE = "public-chosen-type-spell-cost-modifier-v1"
_CLAUSE = re.compile(
    r"(?P<creature>Creature )?spells of the chosen type(?P<controller> you cast)? "
    r"cost (?P<mana>\{(?P<generic>[1-9][0-9]*)\}|(?:\{[WUBRG]\})+) less to cast"
    r"(?P<colored>\. This effect reduces only the amount of colored mana you pay)?\.?",
    re.IGNORECASE,
)


def chosen_type_spell_cost_handler(text: str):
    match = _CLAUSE.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    if match["generic"] is not None and not match["colored"]:
        adjustment = (("GENERIC", -int(match["generic"])),)
    elif match["generic"] is None and match["colored"] and match["controller"]:
        adjustment = _mana_adjustment(match["mana"], direction="less")
    else:
        return None
    modifier = PublicCastCostModifierV2Spec(
        affected_controller=(
            CastCostAffectedController.SOURCE_CONTROLLER if match["controller"]
            else CastCostAffectedController.ALL_PLAYERS
        ),
        predicates_any=(ObjectQuerySpec(types_all=("creature",) if match["creature"] else ()),),
        mana_adjustment=adjustment,
    )
    return CHOSEN_TYPE_CAST_COST_TEMPLATE, {
        "handler_id": CHOSEN_TYPE_CAST_COST_HANDLER_ID, "schema_version": 1,
        "event": "cast.cost.modify", "modifier": modifier.to_dict(),
    }, CHOSEN_TYPE_CAST_COST_CAPABILITY
