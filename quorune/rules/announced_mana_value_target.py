"""Bind an announced X to closed mana-value target domains."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..activation_x import (
    ANNOUNCED_ACTIVATION_X_CAPABILITY, CONTROLLED_LAND_RETURN_X_COST, controlled_land_return_x_choice,
)
from ..replacement.immutable import thaw_value
from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, selected_target_count_descriptor, validate_selected_target_count
from ..targets import target_plan


ANNOUNCED_MANA_VALUE_LIMIT = "announced_mana_value_limit"
ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY = "target.mana_value.announced_cast_x"
OWN_GRAVEYARD_MANA_VALUE_CAPABILITY = "target.mana_value.announced_own_graveyard_x"
DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY = "target.mana_value.announced_distinct_own_graveyard_x"
DISTINCT_REANIMATION_RESULT_VERSION = 2
ACTIVATION_GRAVEYARD_MANA_VALUE_CAPABILITY = "target.mana_value.announced_activation_own_graveyard_x"
_CAST_X_LIMIT = {"schema_version": 1, "parameter": "x", "comparison": "at_most"}
_OWN_GRAVEYARD_X_LIMIT = {"schema_version": 2, "parameter": "x", "comparison": "at_most",
                          "scope": "own_graveyard_creature"}
_ACTIVATION_X_EQUAL = {"schema_version": 3, "parameter": "x", "comparison": "equal",
                       "scope": "noncreature_artifact"}
_DISTINCT_OWN_GRAVEYARD_X_LIMIT = {
    "schema_version": 4, "parameter": "x", "comparison": "at_most",
    "scope": "distinct_own_graveyard_creatures",
}
_OWN_GRAVEYARD_CREATURE_GROUP = {
    "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you", "types_any": ["creature"],
}
_ACTIVATION_GRAVEYARD_X_LIMIT = {"schema_version": 4, "parameter": "x", "comparison": "at_most",
                                 "scope": "own_graveyard_artifact_or_enchantment"}
_FIXED_LIMIT_FIELDS = {"mana_value", "mana_value_min", "mana_value_max", "mana_value_equal"}
_ARTIFACT_DESTROY_PRICE = {
    "text": "{X}{X}{1}", "mana": {"GENERIC": 1, **dict.fromkeys("WUBRGC", 0)},
    "complex_symbols": [], "tap_source": False, "untap_source": False,
    "discard_source": False, "sacrifice_source": False, "exile_source": False,
    "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
    "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    "announced_x_symbols": 2,
}


def exact_artifact_destruction_price(cost_schema) -> bool:
    value = thaw_value(cost_schema)
    return (
        isinstance(value, dict) and value == _ARTIFACT_DESTROY_PRICE
        and all(type(value[key]) is type(expected) for key, expected in _ARTIFACT_DESTROY_PRICE.items())
        and all(type(amount) is int for amount in value["mana"].values())
    )


def exact_land_return_x_activation_price(cost_schema) -> bool:
    from ..activation_condition_model import PUBLIC_ACTIVATION_FIELD, PublicActivationTiming, public_activation_descriptor

    value = thaw_value(cost_schema)
    if not isinstance(value, dict):
        return False
    timing = value.pop(PUBLIC_ACTIVATION_FIELD, None)
    if timing is not None and (timing != public_activation_descriptor(PublicActivationTiming.SORCERY)
                               or type(timing.get("schema_version")) is not int):
        return False
    expected = {**_ARTIFACT_DESTROY_PRICE, "text": CONTROLLED_LAND_RETURN_X_COST,
                "mana": {**dict.fromkeys(("GENERIC", *"WUBRGC"), 0), "W": 1},
                "choices": [controlled_land_return_x_choice().compact()], "announced_x_symbols": 1}
    return (value == expected and all(type(value[key]) is type(part) for key, part in expected.items())
            and all(type(amount) is int for amount in value["mana"].values()))


def has_activation_graveyard_mana_value_limit(schema) -> bool:
    descriptor = schema.get(ANNOUNCED_MANA_VALUE_LIMIT) if isinstance(schema, Mapping) else None
    return (isinstance(descriptor, Mapping) and dict(descriptor) == _ACTIVATION_GRAVEYARD_X_LIMIT
            and type(descriptor["schema_version"]) is int)


def has_activation_mana_value_target(schema) -> bool:
    return has_activation_mana_value_equality(schema) or has_activation_graveyard_mana_value_limit(schema)


def has_activation_mana_value_equality(schema) -> bool:
    descriptor = schema.get(ANNOUNCED_MANA_VALUE_LIMIT) if isinstance(schema, Mapping) else None
    return (isinstance(descriptor, Mapping) and dict(descriptor) == _ACTIVATION_X_EQUAL
            and type(descriptor["schema_version"]) is int)


def distinct_own_graveyard_mana_value_schema() -> dict[str, Any]:
    return {**deepcopy(_OWN_GRAVEYARD_CREATURE_GROUP),
            ANNOUNCED_MANA_VALUE_LIMIT: dict(_DISTINCT_OWN_GRAVEYARD_X_LIMIT)}


def has_distinct_own_graveyard_mana_value_limit(schema: Mapping[str, Any]) -> bool:
    limit = schema.get(ANNOUNCED_MANA_VALUE_LIMIT)
    return isinstance(limit, Mapping) and type(limit.get("schema_version")) is int and limit == _DISTINCT_OWN_GRAVEYARD_X_LIMIT


def bind_announced_mana_value_limit(
    schema: Mapping[str, Any], announced_x: int, *, activation: bool = False,
) -> dict[str, Any]:
    """Materialize the target's normal fixed bound at the chosen X."""
    bound = deepcopy(dict(schema))
    if ANNOUNCED_MANA_VALUE_LIMIT not in bound:
        return bound
    limit = bound.pop(ANNOUNCED_MANA_VALUE_LIMIT)
    supported = (_ACTIVATION_X_EQUAL, _ACTIVATION_GRAVEYARD_X_LIMIT) if activation else (
        _CAST_X_LIMIT, _OWN_GRAVEYARD_X_LIMIT, _DISTINCT_OWN_GRAVEYARD_X_LIMIT,
    )
    if (not isinstance(limit, Mapping) or dict(limit) not in supported
            or type(limit["schema_version"]) is not int
            or type(announced_x) is not int or announced_x < 0
            or _FIXED_LIMIT_FIELDS.intersection(bound)
            or {"groups", "modes", "count_parameter"}.intersection(bound)):
        raise ValueError("Invalid announced X mana-value target")

    if dict(limit) == _ACTIVATION_X_EQUAL:
        from ..compiler.direct_target import DirectPermanentTargetSpec

        expected = DirectPermanentTargetSpec(types_any=("artifact",), types_none=("creature",)).to_target_schema()
        if thaw_value(bound) != expected or type(bound.get("count")) is not int:
            raise ValueError("Announced activation X requires one noncreature artifact")
        return {**bound, "mana_value": announced_x}

    if limit == _DISTINCT_OWN_GRAVEYARD_X_LIMIT:
        if thaw_value(bound) != _OWN_GRAVEYARD_CREATURE_GROUP:
            raise ValueError("Distinct announced values require the original own-graveyard creature group")
        return {**bound, "mana_value_max": announced_x, "up_to": announced_x + 1, "distinct_mana_values": True}

    bound["mana_value_max"] = announced_x
    plan = target_plan(bound, (), require_modes=False)
    if len(plan.groups) != 1:
        raise ValueError("Announced mana-value limit requires one target")
    target, = plan.groups
    if (target.min_targets, target.max_targets) != (1, 1):
        raise ValueError("Announced mana-value limit requires exactly one mandatory target")
    if limit["schema_version"] in {2, 4}:
        supported_types = ("creature",) if limit["schema_version"] == 2 else ("artifact", "enchantment")
        if (set(bound) != {"zones", "categories", "count", "owner_relation", "types_any", "mana_value_max"}
                or type(bound["count"]) is not int
                or target.zones != ("graveyard",) or target.categories != ("card",)
                or target.owner_relation != "you" or target.types_any != supported_types):
            raise ValueError("The announced upper bound requires its exact owned graveyard target")
    elif target.zones != ("battlefield",) or target.categories != ("permanent",):
        raise ValueError("Announced mana-value limit requires a permanent target")
    return bound


def declare_announced_mana_value_limit(
    fixed_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve an admitted fixed target shape while switching its upper bound to cast X."""
    declared = deepcopy(dict(fixed_schema))
    sample_bound = declared.pop("mana_value_max", None)
    if ANNOUNCED_MANA_VALUE_LIMIT in declared or type(sample_bound) is not int or sample_bound < 0:
        raise ValueError("An announced mana-value limit needs a closed fixed upper bound")
    declared[ANNOUNCED_MANA_VALUE_LIMIT] = dict(_CAST_X_LIMIT)
    bind_announced_mana_value_limit(declared, sample_bound)
    return declared


def declare_own_graveyard_mana_value_limit(fixed_schema: Mapping[str, Any]):
    target = deepcopy(dict(fixed_schema))
    amount = target.pop("mana_value_max", None)
    if ANNOUNCED_MANA_VALUE_LIMIT in target or type(amount) is not int or amount < 0:
        raise ValueError("An announced graveyard limit requires a closed fixed upper bound")
    target[ANNOUNCED_MANA_VALUE_LIMIT] = dict(_OWN_GRAVEYARD_X_LIMIT)
    bind_announced_mana_value_limit(target, amount)
    return target


def declare_activation_graveyard_mana_value_limit(fixed_schema: Mapping[str, Any]) -> dict[str, Any]:
    target = deepcopy(dict(fixed_schema))
    amount = target.pop("mana_value_max", None)
    if ANNOUNCED_MANA_VALUE_LIMIT in target or type(amount) is not int or amount < 0:
        raise ValueError("An announced graveyard limit requires a closed fixed upper bound")
    target[ANNOUNCED_MANA_VALUE_LIMIT] = dict(_ACTIVATION_GRAVEYARD_X_LIMIT)
    bind_announced_mana_value_limit(target, amount, activation=True)
    return target


def declare_activation_mana_value_equality(fixed_schema: Mapping[str, Any]) -> dict[str, Any]:
    declared = deepcopy(dict(fixed_schema))
    sample = declared.pop("mana_value", None)
    if ANNOUNCED_MANA_VALUE_LIMIT in declared or type(sample) is not int or sample < 0:
        raise ValueError("Announced activation X needs one exact mana-value target")
    declared[ANNOUNCED_MANA_VALUE_LIMIT] = dict(_ACTIVATION_X_EQUAL)
    bind_announced_mana_value_limit(declared, sample, activation=True)
    return declared


def has_announced_mana_value_limit(schema: Any) -> bool:
    return isinstance(schema, Mapping) and ANNOUNCED_MANA_VALUE_LIMIT in schema


def announced_mana_value_capability(schema: Mapping[str, Any]) -> str:
    if has_distinct_own_graveyard_mana_value_limit(schema):
        return DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY
    if has_activation_graveyard_mana_value_limit(schema):
        return ACTIVATION_GRAVEYARD_MANA_VALUE_CAPABILITY
    if has_activation_mana_value_equality(schema):
        return ANNOUNCED_ACTIVATION_X_CAPABILITY
    limit = schema.get(ANNOUNCED_MANA_VALUE_LIMIT)
    if isinstance(limit, Mapping) and dict(limit) == _OWN_GRAVEYARD_X_LIMIT:
        return OWN_GRAVEYARD_MANA_VALUE_CAPABILITY
    return ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY


def distinct_graveyard_reanimation_effect() -> dict[str, Any]:
    return {
        "op": "reanimate_targets",
        "cards": "$targets",
        "maximum_targets": selected_target_count_descriptor(),
        "announced_target_count": True,
        "schema_version": DISTINCT_REANIMATION_RESULT_VERSION,
        "battlefield_controller": "actor",
        "battlefield_tapped": False,
    }


def uses_distinct_graveyard_reanimation(effect: Mapping[str, Any]) -> bool:
    return effect.get("op") == "reanimate_targets" and effect.get("schema_version") == DISTINCT_REANIMATION_RESULT_VERSION


def _project_distinct_graveyard_reanimation(effects, schema, mechanics):
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities

    if len(effects) != 1:
        raise ValueError("Distinct graveyard reanimation requires one set result")
    effect = thaw_value(effects[0])
    if (
        effect != distinct_graveyard_reanimation_effect()
        or type(effect["schema_version"]) is not int
        or effect["announced_target_count"] is not True
        or effect["battlefield_tapped"] is not False
    ):
        raise ValueError("Distinct graveyard mana values require their exact announced reanimation result")
    validate_selected_target_count(effect["maximum_targets"])
    fixed = {key: value for key, value in effect.items() if key != "announced_target_count"}
    fixed.update(maximum_targets=2, schema_version=1)
    target = bind_announced_mana_value_limit(schema, 1)
    del target["distinct_mana_values"]
    if not fixed_homogeneous_target_set_node_capabilities(effects=(fixed,), target_schema=target, mechanic_ids=mechanics):
        raise ValueError("Distinct graveyard reanimation requires its existing simultaneous entry authority")
    return fixed, target


def project_announced_mana_value_target(effects, schema, mechanics):
    """Validate the target extension before the fixed result owners inspect it."""
    if not has_announced_mana_value_limit(schema) or len(effects) != 1:
        raise ValueError("The announced mana-value target requires one represented result")
    if has_distinct_own_graveyard_mana_value_limit(schema):
        return _project_distinct_graveyard_reanimation(effects, schema, mechanics)[1]
    activation = has_activation_mana_value_target(schema)
    bound = bind_announced_mana_value_limit(schema, 0, activation=activation)
    if has_activation_mana_value_equality(schema):
        from .node_capability_shapes import targeted_destruction_node_capabilities

        if effects[0] != {"op": "destroy", "card": "$target.0"}:
            raise ValueError("The announced activation target needs its exact artifact destruction result")
        owns_result = targeted_destruction_node_capabilities
    elif announced_mana_value_capability(schema) in {
        OWN_GRAVEYARD_MANA_VALUE_CAPABILITY, ACTIVATION_GRAVEYARD_MANA_VALUE_CAPABILITY,
    }:
        from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities

        if has_activation_graveyard_mana_value_limit(schema) and dict(effects[0]) != {
            "op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False,
        }:
            raise ValueError("Announced activation X requires its ordinary graveyard return")
        owns_result = fixed_target_reanimation_node_capabilities
    else:
        from .control_capability_shapes import fixed_control_node_capabilities
        from .node_capability_shapes import targeted_exile_node_capabilities

        operation = effects[0].get("op")
        if operation not in {"exile_permanent", "gain_control"}:
            raise ValueError("The battlefield limit requires its exact displacement result")
        owns_result = (targeted_exile_node_capabilities if operation == "exile_permanent"
                       else fixed_control_node_capabilities)
    if not owns_result(effects=effects, target_schema=bound, mechanic_ids=mechanics):
        raise ValueError("The announced mana-value limit requires a closed fixed target result")
    return bound


def _activation_catalog_matches_declared_target(program) -> bool:
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    if (len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
            or program.handlers[0].get("requires_complete_card_program") is not True):
        return False
    ability, = activated_abilities_from_descriptors(program.handlers)
    graveyard = has_activation_graveyard_mana_value_limit(program.target_schema)
    price_is_closed = exact_land_return_x_activation_price if graveyard else exact_artifact_destruction_price
    return (ability.zones == ("battlefield",) and not ability.mana_ability and ability.sorcery_speed is graveyard
            and not ability.activation_conditions and ability.builtin_semantic_key == program.key
            and ability.generic_reduction_per_legendary_creature == 0 and not ability.library_search_types
            and thaw_value(ability.target_schema) == thaw_value(program.target_schema)
            and price_is_closed(activated_ability_cost(ability)))


def project_announced_target_program(program):
    """Expose the existing fixed shape for admission and runtime dependency inference."""
    if has_activation_mana_value_target(program.target_schema):
        price_is_closed = (exact_land_return_x_activation_price if has_activation_graveyard_mana_value_limit(program.target_schema)
                           else exact_artifact_destruction_price)
        if ((program.event, program.active_zone) != ("activate", "battlefield")
                or not program.ability_id.startswith("ability:") or not _activation_catalog_matches_declared_target(program)
                or program.event_condition is not None or program.destination is not None
                or not price_is_closed(program.cost_schema)):
            raise ValueError("Announced activation targets require their exact ordinary battlefield price")
    elif (program.event != "resolve" or program.active_zone != "stack"
          or not program.ability_id.startswith("spell:")):
        raise ValueError("Announced mana-value targets require a resolving spell")
    if has_distinct_own_graveyard_mana_value_limit(program.target_schema):
        effect, target = _project_distinct_graveyard_reanimation(program.effects, program.target_schema, program.coverage)
        return replace(program, effects=[effect], target_schema=target)
    return replace(
        program,
        target_schema=project_announced_mana_value_target(
            program.effects, program.target_schema, program.coverage,
        ),
    )


def announced_mana_value_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    from .capabilities import capability_dependencies_for_node

    if has_activation_mana_value_equality(target_schema) and not exact_artifact_destruction_price(cost_schema):
        return ()
    if has_activation_graveyard_mana_value_limit(target_schema) and not exact_land_return_x_activation_price(cost_schema):
        return ()
    try:
        if has_distinct_own_graveyard_mana_value_limit(target_schema):
            effect, fixed_schema = _project_distinct_graveyard_reanimation(effects, target_schema, mechanics)
            effects = (effect,)
        else:
            fixed_schema = project_announced_mana_value_target(effects, target_schema, mechanics)
    except (TypeError, ValueError):
        return ()
    fixed = capability_dependencies_for_node(
        effects=effects, target_schema=fixed_schema, cost_schema=cost_schema,
        mechanic_ids=mechanics,
    )
    declared_count = (ANY_NUMBER_TARGET_CAPABILITY,) if has_distinct_own_graveyard_mana_value_limit(target_schema) else ()
    return tuple(sorted({announced_mana_value_capability(target_schema), *fixed, *declared_count}))
