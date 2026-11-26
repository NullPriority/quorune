from __future__ import annotations

"""One paid temporary lifelink grant to an actual designated commander."""

from collections.abc import Mapping

from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..replacement.immutable import FrozenMap, thaw_value


CAPABILITY = "continuous.ability.temporary_commander_lifelink"
_TEXT = "target commander gains lifelink until end of turn."
_PRICE = {
    "text": "{2}, {T}", "mana": {**dict.fromkeys("WUBRGC", 0), "GENERIC": 2},
    "complex_symbols": [], "tap_source": True, "untap_source": False,
    "discard_source": False, "sacrifice_source": False, "exile_source": False,
    "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
    "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def commander_lifelink_instruction():
    from .temporary_ability_grant import OPERATION

    effect = {"op": OPERATION, "schema_version": 7, "card": "$target.0",
              "keywords": ["Lifelink"], "ability_fragments": [], "exclude_source": False, "printed_only": False}
    schema = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "commander": True}
    return effect, schema


def commander_lifelink_price_is_closed(cost):
    return (isinstance(cost, Mapping) and thaw_value(FrozenMap(cost)) == _PRICE
            and all(type(cost[key]) is type(value) for key, value in _PRICE.items() if key not in {"mana", "choices", "complex_symbols", "uncompiled_costs"})
            and all(type(amount) is int for amount in cost["mana"].values()))


def commander_lifelink_activation_matches(ability, text):
    from ..compiler.activated_costs import activated_ability_cost

    return (
        " ".join(text.casefold().split()) == _TEXT
        and ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and not ability.activation_conditions and ability.activation_limit is None
        and commander_lifelink_price_is_closed(activated_ability_cost(ability))
    )


def commander_lifelink_node_capabilities(effect, schema, mechanics, cost):
    from .temporary_ability_grant import TEMPORARY_QUOTED_GRANT_CAPABILITY, TEMPORARY_QUOTED_GRANT_MECHANIC

    expected, target = commander_lifelink_instruction()
    if (FrozenMap(effect) != FrozenMap(expected) or type(effect.get("schema_version")) is not int
            or effect.get("exclude_source") is not False or effect.get("printed_only") is not False
            or not isinstance(schema, Mapping) or FrozenMap(schema) != FrozenMap(target)
            or schema.get("commander") is not True or type(schema.get("count")) is not int
            or not {TEMPORARY_QUOTED_GRANT_MECHANIC, "lifelink", "cr-115-targets", "cr-611-continuous-effects"} <= set(mechanics)
            or not commander_lifelink_price_is_closed(cost)):
        return ()
    return (CAPABILITY, TEMPORARY_QUOTED_GRANT_CAPABILITY,
            "state_query.commander.permanent_designation", "target.revalidate_resolution",
            "continuous.resolution.fixed_characteristics_until_end_of_turn",
            *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Lifelink"])


def commander_lifelink_runtime_dependencies(program):
    from ..abilities import ActivatedAbility
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    from .temporary_ability_grant import OPERATION

    schema = program.target_schema
    marked = CAPABILITY in program.capability_dependencies or (
        isinstance(schema, Mapping) and schema.get("commander") is True
        and any(effect.get("op") == OPERATION and "Lifelink" in effect.get("keywords", ()) for effect in program.effects)
    )
    if not marked:
        return ()
    catalog = tuple(handler["ability"] for handler in program.handlers
                    if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    if ((program.event, program.active_zone) != ("activate", "battlefield") or program.event_condition is not None
            or len(catalog) != 1 or len(program.handlers) != 1 or len(program.effects) != 1):
        raise ValueError("Commander lifelink requires its exact battlefield activation and catalog")
    ability = ActivatedAbility.from_dict(thaw_value(catalog[0]))
    required = commander_lifelink_node_capabilities(program.effects[0], schema, program.coverage, program.cost_schema)
    if not required or not commander_lifelink_activation_matches(ability, ability.effect_text):
        raise ValueError("Commander lifelink requires its original paid tap and exact commander recipient")
    return required
