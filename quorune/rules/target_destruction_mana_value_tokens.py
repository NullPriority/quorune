"""A fixed creature-token definition scaled by the original destruction target."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_KIND, ScalarAmountOrigin, ScalarEffectAmountSpec
from .committed_quantity_followups import fixed_followup_capabilities
from .effect_shape_walkers import nested_effects


TARGET_DESTRUCTION_MANA_TOKEN_CAPABILITY = "resolution.effect_program.destroy_target_mana_value_tokens"
TARGET_DESTRUCTION_MANA_TOKEN_AMOUNT = ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET, characteristic="mana_value")


def uses_target_destruction_mana_tokens(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != "create_token":
        return False
    quantity = effect.get("quantity")
    return (isinstance(quantity, Mapping) and quantity.get("kind") == SCALAR_AMOUNT_KIND
            and quantity.get("origin") == ScalarAmountOrigin.TARGET.value and quantity.get("characteristic") == "mana_value")


def _target_destruction_scope(first, schema):
    if not isinstance(schema, Mapping) or type(schema.get("count")) is not int:
        return None
    ordinary = {"op": "destroy", "card": "$target.0"}
    baseline = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
    if (FrozenMap(schema) == FrozenMap({**baseline, "types_any": ["artifact"]})
            and dict(first) == {**ordinary, "regeneration_prohibited": True}
            and first["regeneration_prohibited"] is True):
        return "permanent.destroy.regeneration_prohibition"
    if FrozenMap(schema) == FrozenMap({**baseline, "types_any": ["enchantment"]}) and dict(first) == ordinary:
        return "permanent.destroy.effect"
    return None


def target_destruction_mana_token_node_capabilities(*, effects, target_schema, cost_schema=None):
    if (cost_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping)
            or not uses_target_destruction_mana_tokens(effects[1])):
        return ()
    first, token = effects
    destruction = _target_destruction_scope(first, target_schema)
    definition = token.get("characteristics")
    if (destruction is None or set(token) != {"op", "controller", "quantity", "name", "characteristics"}
            or token["controller"] != "$controller" or not isinstance(definition, Mapping)
            or not str(definition.get("type_line", "")).startswith("Token Creature — ")):
        return ()
    try:
        if ScalarEffectAmountSpec.from_dict(token["quantity"]) != TARGET_DESTRUCTION_MANA_TOKEN_AMOUNT:
            return ()
        token_caps = fixed_followup_capabilities({**token, "quantity": 1})
    except (TypeError, ValueError):
        return ()
    if not token_caps:
        return ()
    from ..compiler.bound_effect_programs import BOUND_EFFECT_PROGRAM_CAPABILITY
    from .closed_effect_program_shapes import CLOSED_EFFECT_PROGRAM_CAPABILITY

    return tuple(sorted({TARGET_DESTRUCTION_MANA_TOKEN_CAPABILITY, SCALAR_AMOUNT_CAPABILITY,
        BOUND_EFFECT_PROGRAM_CAPABILITY, CLOSED_EFFECT_PROGRAM_CAPABILITY, "quantity_expression.declared_effect_amount",
        "permanent.destroy.effect", destruction, "target.revalidate_resolution", *token_caps}))


def target_destruction_mana_token_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_target_destruction_mana_tokens(effect))
    if not marked and TARGET_DESTRUCTION_MANA_TOKEN_CAPABILITY not in program.capability_dependencies:
        return ()
    required = target_destruction_mana_token_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if (not required or marked != tuple(program.effects[1:]) or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Target mana-value tokens require the original complete one-target destruction spell")
    return required
