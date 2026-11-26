from __future__ import annotations

"""Closed private search and its source-bound Bargain result."""

from collections.abc import Mapping

from ..bargain_model import BARGAIN_CAPABILITY, BARGAIN_INSTANCE
from ..declared_cast_cost_model import DECLARED_CAST_COST_CAPABILITY, DECLARED_CAST_COSTS_FIELD, DeclaredCastCostKind
from ..declared_cost_conditions import DeclaredCastCostCondition
from ..library_search_model import BARGAIN_SEARCH_VERSION, is_bargain_search_effect
from ..mana_value_limit import ManaValueLimitSpec
from ..replacement.immutable import FrozenMap


BARGAIN_SEARCH_CAST_OPERATION = "bargain_search_cast_choice"
BARGAIN_SEARCH_MECHANIC = "bargained-private-search-cast"
BARGAIN_SEARCH_CAPABILITY = "library.search.private_face_down_cast_or_hand"
BARGAIN_SEARCH_CAPABILITIES = (BARGAIN_SEARCH_CAPABILITY, BARGAIN_CAPABILITY, DECLARED_CAST_COST_CAPABILITY)


def bargain_search_effect(maximum: int) -> dict:
    return {
        "op": "search", "schema_version": BARGAIN_SEARCH_VERSION,
        "zone": "library", "selector": {}, "count": {"minimum": 1, "maximum": 1},
        "destination": "exile", "reveal": False, "shuffle_after": True,
        "cast_limit": ManaValueLimitSpec(maximum).to_dict(),
    }


def bargain_search_limit(effect: Mapping) -> ManaValueLimitSpec:
    if not is_bargain_search_effect(effect) or type(effect["schema_version"]) is not int:
        raise ValueError("Private Bargain search needs its versioned instruction")
    limit = ManaValueLimitSpec.from_dict(effect.get("cast_limit"))
    count = effect.get("count")
    if (FrozenMap(effect) != FrozenMap(bargain_search_effect(limit.maximum))
            or not isinstance(count, Mapping) or any(type(value) is not int for value in count.values())
            or effect["reveal"] is not False or effect["shuffle_after"] is not True):
        raise ValueError("Bargain search requires one private face-down result and a mandatory shuffle")
    return limit


def was_search_spell_bargained(context: Mapping) -> bool:
    raw = context.get(DECLARED_CAST_COSTS_FIELD)
    if raw is None:
        return False
    return DeclaredCastCostCondition(DeclaredCastCostKind.BARGAIN, BARGAIN_INSTANCE).matches(raw)


def bargain_search_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or BARGAIN_SEARCH_MECHANIC not in mechanic_ids:
        return ()
    try:
        bargain_search_limit(effects[0])
    except (TypeError, ValueError):
        return ()
    return BARGAIN_SEARCH_CAPABILITIES


def bargain_search_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if (BARGAIN_SEARCH_CAPABILITY not in program.capability_dependencies
            and BARGAIN_SEARCH_MECHANIC not in program.coverage
            and not any(is_bargain_search_effect(effect)
                        for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    if (program.event, program.active_zone) == ("resolve", "stack") and not program.handlers:
        required = bargain_search_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=(BARGAIN_SEARCH_MECHANIC,),
        )
        if required:
            return required
    raise ValueError("Bargain search requires its original single untargeted spell effect")
