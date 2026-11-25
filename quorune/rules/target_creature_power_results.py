"""One exact draw or life result from the legal target creature's power."""

from collections.abc import Mapping

from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY,
    TARGET_CREATURE_POWER_CAPABILITY,
    TARGET_CREATURE_POWER_VERSION,
    ScalarAmountOrigin,
    ScalarEffectAmountSpec,
    scalar_amount_specs,
)


def target_creature_power_effect(operation):
    amount = ScalarEffectAmountSpec(
        ScalarAmountOrigin.TARGET_CREATURE_POWER, characteristic="power", schema_version=TARGET_CREATURE_POWER_VERSION,
    ).to_dict()
    if operation == "draw":
        return {"op": "draw", "player": "$controller", "count": amount, "private": True}
    if operation == "life":
        return {"op": "life", "player": "$controller", "delta": amount}
    raise ValueError("Target creature power requires one ordinary draw or life gain")


def target_creature_power_node_capabilities(*, effects, target_schema, cost_schema=None):
    if (cost_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping)
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int):
        return ()
    from ..compiler.direct_target import direct_permanent_target_spec
    from ..compiler.optional_effect_templates import FIXED_OPTIONAL_EFFECT_CAPABILITY, OPTIONAL_EFFECT_OPERATION

    effect = effects[0]
    is_optional = effect.get("op") == OPTIONAL_EFFECT_OPERATION
    optional_capabilities = ()
    if is_optional:
        children = effect.get("effects")
        if (set(effect) != {"op", "player", "effects"} or effect["player"] != "$controller"
                or not isinstance(children, (tuple, list)) or len(children) != 1 or not isinstance(children[0], Mapping)):
            return ()
        effect = children[0]
        optional_capabilities = (FIXED_OPTIONAL_EFFECT_CAPABILITY,)
    operation = effect.get("op")
    if operation not in ("draw", "life"):
        return ()
    slot = "count" if operation == "draw" else "delta"
    amount = effect.get(slot)
    if (not isinstance(amount, Mapping) or amount.get("origin") != ScalarAmountOrigin.TARGET_CREATURE_POWER
            or effect != target_creature_power_effect(operation)):
        return ()
    if operation == "draw" and effect["private"] is not True:
        return ()
    try:
        ScalarEffectAmountSpec.from_dict(amount)
    except (TypeError, ValueError):
        return ()
    controlled = direct_permanent_target_spec("target creature you control").to_target_schema()
    any_creature = direct_permanent_target_spec("target creature").to_target_schema()
    if not (target_schema == controlled or operation == "life" and not is_optional and target_schema == any_creature):
        return ()
    if operation == "draw" and is_optional:
        return ()
    result = "zone.draw.library_to_hand" if operation == "draw" else "life.change.effect"
    return (TARGET_CREATURE_POWER_CAPABILITY, SCALAR_AMOUNT_CAPABILITY,
            "target.revalidate_resolution", result, *optional_capabilities)


def target_creature_power_runtime_dependencies(program):
    values = (program.effects, getattr(program, "handlers", ()))
    if not any(spec.origin is ScalarAmountOrigin.TARGET_CREATURE_POWER for spec in scalar_amount_specs(values)):
        return ()
    dependencies = target_creature_power_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if not dependencies or getattr(program, "handlers", ()):
        raise ValueError("Target creature power requires its exact creature target and one controller result")
    return dependencies
