"""Printed cast-X token creation with a protected committed-token destruction result."""

from collections.abc import Mapping

from ..fixed_token_production import fixed_token_effect_shape_is_closed
from ..query_effect_amount_model import CastXAmountSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import contains_nested_key
from .graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities


TOKEN_SWEEP_FIELD = "cast_x_created_tokens_sweep"
TOKEN_SWEEP_MECHANIC = "cast-x-created-tokens-sweep"
TOKEN_SWEEP_CAPABILITY = "permanent.destroy.except_created_cast_x_tokens"
TOKEN_SWEEP_RESULT_FIELD = "_committed_token_sweep"
TOKEN_SWEEP_ORDER_FIELD = "_token_sweep_owner_orders"


def announced_token_sweep_effect(token, minimum):
    if type(minimum) is not int or not 1 <= minimum <= 20:
        raise ValueError("A token sweep requires a positive fixed X threshold")
    return {**token, "quantity": CastXAmountSpec().to_dict(),
            TOKEN_SWEEP_FIELD: {"schema_version": 1, "minimum": minimum}}


def token_sweep_node_capabilities(*, effects, target_schema, mechanic_ids):
    if TOKEN_SWEEP_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    marker = effect.get(TOKEN_SWEEP_FIELD)
    if (
        not isinstance(marker, Mapping) or set(marker) != {"schema_version", "minimum"}
        or type(marker["schema_version"]) is not int or marker["schema_version"] != 1
        or type(marker["minimum"]) is not int or not 1 <= marker["minimum"] <= 20
        or FrozenMap(effect.get("quantity") or {}) != FrozenMap(CastXAmountSpec().to_dict())
    ):
        return ()
    token = {key: thaw_value(value) for key, value in effect.items() if key != TOKEN_SWEEP_FIELD}
    token["quantity"] = 1
    if (
        not fixed_token_effect_shape_is_closed(token)
        or "Creature" not in str(token["characteristics"].get("type_line", "")).partition(" — ")[0].split()
    ):
        return ()
    dependencies = fixed_token_creation_node_capabilities(
        effects=(token,), target_schema=None, mechanic_ids={"fixed-token-production", "cr-111-tokens"},
    )
    return (TOKEN_SWEEP_CAPABILITY, "quantity_expression.declared_effect_amount",
            "permanent.destroy.fixed_set", "zone.change.destination_replacement",
            GRAVEYARD_ARRIVAL_ORDER_CAPABILITY, *dependencies) if dependencies else ()


def token_sweep_program_dependencies(program):
    if any(contains_nested_key((program.effects, program.handlers), key)
           for key in (TOKEN_SWEEP_RESULT_FIELD, TOKEN_SWEEP_ORDER_FIELD)):
        raise ValueError("A committed sweep result cannot occur in compiled instructions")
    if not (contains_nested_key((program.effects, program.handlers), TOKEN_SWEEP_FIELD)
            or TOKEN_SWEEP_CAPABILITY in program.capability_dependencies):
        return ()
    dependencies = token_sweep_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not dependencies or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("A cast-X creature sweep requires its original complete untargeted spell")
    return dependencies


def is_closed_token_sweep_program(program):
    try:
        required = token_sweep_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def require_resolving_token_sweep(host, item):
    program = host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (
        item is None or item.kind not in {"spell", "spell_copy"} or item.targets
        or item.context.get("currently_resolving") is not True or program is None
        or not host.semantic_program_is_current_trusted(program) or not is_closed_token_sweep_program(program)
        or type(item.x_value) is not int or item.x_value < 0
    ):
        raise ValueError("A creature sweep must retain its original resolving spell and cast X")
    return program


def create_tokens_for_sweep(host, item, effect):
    program = require_resolving_token_sweep(host, item)
    actual = {key: value for key, value in effect.items() if key not in {"_replacement_selections", "_runtime_source"}}
    expected = host._semantic_value(thaw_value(program.effects[0]), item)
    if FrozenMap(actual) != FrozenMap(expected) or type(actual["quantity"]) is not int or actual["quantity"] != item.x_value:
        raise ValueError("The token instruction changed the original cast X or token definition")
    creation = {key: value for key, value in effect.items() if key != TOKEN_SWEEP_FIELD}
    created = host.apply_effect(creation, actor=item.controller, as_cost=False)
    if item.x_value < actual[TOKEN_SWEEP_FIELD]["minimum"]:
        return ()
    if not isinstance(created, (list, tuple)) or any(type(ref) is not str or not ref for ref in created):
        raise ValueError("The sweep requires the canonical committed token references")
    by_ref = {card.ref: card for card in host.state.cards.values()}
    identities = sorted((card.object_id, card.logical_object_id) for ref in set(created)
                        if (card := by_ref.get(ref)) is not None and card.zone == "battlefield")
    return ({"op": "choose_objects", "player": item.controller, TOKEN_SWEEP_RESULT_FIELD: {
        "schema_version": 1, "stack_ref": item.ref, "controller": item.controller,
        "cast_x": item.x_value, "protected": identities,
    }},)

def validate_token_sweep_result(host, item, effect):
    from ..ordered_incarnation_exclusion import OrderedIncarnationExclusion

    from .gift_graveyard_spells import SWEEP_RESULT_VERSION, validate_gift_sweep_result

    result = effect[TOKEN_SWEEP_RESULT_FIELD]
    if isinstance(result, Mapping) and result.get("schema_version") == SWEEP_RESULT_VERSION:
        return validate_gift_sweep_result(host, item, effect)
    program = require_resolving_token_sweep(host, item)
    if (set(effect) - {"op", "player", TOKEN_SWEEP_RESULT_FIELD, TOKEN_SWEEP_ORDER_FIELD}
            or effect.get("op") != "choose_objects" or not isinstance(result, Mapping)
            or set(result) != {"schema_version", "stack_ref", "controller", "cast_x", "protected"}
            or type(result["schema_version"]) is not int or result["schema_version"] != 1
            or result["stack_ref"] != item.ref or result["controller"] != item.controller
            or type(result["cast_x"]) is not int or result["cast_x"] != item.x_value
            or item.x_value < program.effects[0][TOKEN_SWEEP_FIELD]["minimum"]
            or (TOKEN_SWEEP_ORDER_FIELD not in effect and effect.get("player") != item.controller)):
        raise ValueError("A sweep result must retain its original spell, caster and X threshold")
    OrderedIncarnationExclusion.from_dict({"schema_version": 1, "protected": result["protected"]})
