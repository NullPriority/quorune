"""Closed Ferocious clause that adds a keyword to one announced creature."""

from collections.abc import Mapping

from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..resolution_conditions import RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison


FEROCIOUS_KEYWORD_CAPABILITY = "resolution.effect.ferocious_same_target_keywords"
FEROCIOUS_KEYWORD_TEMPLATE = "ferocious-same-target-keywords-v1"
_CHARACTERISTIC_MECHANICS = ("cr-611-continuous-effects", "cr-115-targets")
_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"],
})


def ferocious_creature_condition():
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS,
        predicate=ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",),
            numeric_characteristic=TargetNumericCharacteristicSpec(
                TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_LEAST, 4,
            ),
        ),
        schema_version=5,
    )


def _keyword(keyword):
    return {"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": keyword}


def ferocious_keyword_append_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    required_mechanics = {
        RESOLUTION_CONDITION_MECHANIC, *_CHARACTERISTIC_MECHANICS,
        "double-strike", "trample",
    }
    if (len(effects) != 2 or FrozenMap(target_schema or {}) != _TARGET
            or not required_mechanics.issubset(mechanic_ids) or cost_schema is not None):
        return ()
    prefix, conditional = effects
    expected = {
        "op": RESOLUTION_CONDITION_OPERATION, "player": "$controller",
        "condition": ferocious_creature_condition().to_dict(), "effects": [_keyword("Trample")],
        "mechanic_ids": [*_CHARACTERISTIC_MECHANICS, "trample"],
        "prefix_mechanic_ids": [*_CHARACTERISTIC_MECHANICS, "double-strike"],
    }
    if FrozenMap(prefix) != FrozenMap(_keyword("Double Strike")) or FrozenMap(conditional) != FrozenMap(expected):
        return ()
    return (
        FEROCIOUS_KEYWORD_CAPABILITY, RESOLUTION_CONDITION_CAPABILITY, "condition.creature.fixed_numeric_exists",
        "continuous.resolution.fixed_characteristics_until_end_of_turn", "target.revalidate_resolution",
        "combat.damage.participation.strike_steps", "combat.damage.assignment.trample",
    )


def uses_ferocious_keyword_append(effects):
    if len(effects) != 2 or FrozenMap(effects[0]) != FrozenMap(_keyword("Double Strike")):
        return False
    wrapper = effects[1]
    children = wrapper.get("effects")
    return (
        wrapper.get("op") == RESOLUTION_CONDITION_OPERATION and "schema_version" not in wrapper
        and isinstance(children, (list, tuple))
        and len(children) == 1 and isinstance(children[0], Mapping)
        and FrozenMap(children[0]) == FrozenMap(_keyword("Trample"))
    )


def ferocious_keyword_append_program_dependencies(program):
    marked = (FEROCIOUS_KEYWORD_CAPABILITY in program.capability_dependencies
              or program.provenance.get("template_id") == FEROCIOUS_KEYWORD_TEMPLATE)
    if not marked and not uses_ferocious_keyword_append(program.effects):
        return ()
    capabilities = ferocious_keyword_append_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        cost_schema=program.cost_schema,
    )
    if (not capabilities or (program.event, program.active_zone) != ("resolve", "stack")
            or program.provenance.get("template_id") != FEROCIOUS_KEYWORD_TEMPLATE
            or program.event_condition is not None or program.handlers):
        raise ValueError("Ferocious keyword continuation requires the original resolving creature-target spell")
    return capabilities
