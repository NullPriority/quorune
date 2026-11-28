"""An ordinary artifact destruction followed by its original controller comparison."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..target_controller_information import TARGET_CONTROLLER_INFORMATION_CAPABILITY, target_controller_reference
from .committed_quantity_followups import fixed_followup_capabilities
from .effect_shape_walkers import nested_effects


DESTROY_CONTROLLED_ARTIFACT_TOKEN_CAPABILITY = "resolution.effect_program.destroy_controlled_artifact_tokens"
DESTROY_CONTROLLED_ARTIFACT_TOKEN_VERSION = 35
_TARGET_CONTROLLER_EQUALS_RESOLVER = "original_target_controller_equals_resolver"
_CONDITION_OPERATION = "apply_if_public_condition"
_TARGET_SCHEMA = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["artifact"],
})


def controlled_artifact_token_branch(token):
    return {
        "op": _CONDITION_OPERATION, "schema_version": DESTROY_CONTROLLED_ARTIFACT_TOKEN_VERSION,
        "player": "$controller",
        "condition": {"kind": _TARGET_CONTROLLER_EQUALS_RESOLVER, "reference": target_controller_reference()},
        "effects": [token],
    }


def uses_controlled_artifact_token_branch(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != _CONDITION_OPERATION:
        return False
    condition = effect.get("condition")
    return (effect.get("schema_version") == DESTROY_CONTROLLED_ARTIFACT_TOKEN_VERSION
            or isinstance(condition, Mapping) and condition.get("kind") == _TARGET_CONTROLLER_EQUALS_RESOLVER)


def _branch_parts(effect, *, actor=None):
    if (not uses_controlled_artifact_token_branch(effect)
            or set(effect) != {"op", "schema_version", "player", "condition", "effects"}
            or type(effect["schema_version"]) is not int or effect["schema_version"] != DESTROY_CONTROLLED_ARTIFACT_TOKEN_VERSION
            or effect["player"] != ("$controller" if actor is None else actor)
            or not isinstance(effect["effects"], (list, tuple)) or len(effect["effects"]) != 1):
        raise ValueError("Controlled-artifact tokens require their original closed resolution branch")
    condition = effect["condition"]
    if (not isinstance(condition, Mapping) or set(condition) != {"kind", "reference"}
            or condition.get("kind") != _TARGET_CONTROLLER_EQUALS_RESOLVER):
        raise ValueError("Controlled-artifact tokens require one original target controller comparison")
    reference = condition["reference"]
    if actor is None:
        legal_reference = (isinstance(reference, Mapping) and type(reference.get("schema_version")) is int
                           and FrozenMap(reference) == FrozenMap(target_controller_reference()))
    else:
        legal_reference = type(reference) is str and bool(reference) and not reference.startswith("$")
    if not legal_reference:
        raise ValueError("Controlled-artifact tokens lost the original target's controller")
    token, = effect["effects"]
    if (not isinstance(token, Mapping) or set(token) != {"op", "controller", "quantity", "name", "characteristics"}
            or token.get("op") != "create_token" or token["controller"] != effect["player"]
            or type(token["quantity"]) is not int or token["quantity"] != 3
            or not isinstance(token["characteristics"], Mapping)
            or not str(token["characteristics"].get("type_line", "")).startswith("Token Creature — ")):
        raise ValueError("Controlled-artifact tokens require three ordinary fixed creature tokens")
    capabilities = fixed_followup_capabilities({**token, "controller": "$controller", "quantity": 1})
    if not capabilities:
        raise ValueError("Controlled-artifact tokens require the stock creature token owner")
    return reference, token, capabilities


def controlled_artifact_token_effects(effect, *, actor, stack_controller):
    if type(actor) is not str or not actor or actor.startswith("$") or actor != stack_controller:
        raise ValueError("The original resolving controller owns the conditional token result")
    controller, token, _capabilities = _branch_parts(effect, actor=actor)
    return (FrozenMap(token),) if controller == actor else ()


def controlled_artifact_token_node_capabilities(*, effects, target_schema, cost_schema=None):
    if (cost_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping)
            or dict(effects[0]) != {"op": "destroy", "card": "$target.0"}
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != _TARGET_SCHEMA):
        return ()
    try:
        _target_controller, _token, token_caps = _branch_parts(effects[1])
    except (TypeError, ValueError):
        return ()
    return tuple(sorted({DESTROY_CONTROLLED_ARTIFACT_TOKEN_CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY,
        "permanent.destroy.effect", "resolution.effect.public_condition", "resolution.effect_program.bound_references",
        "resolution.effect_program.closed_components", "target.revalidate_resolution", *token_caps}))


def controlled_artifact_token_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_controlled_artifact_token_branch(effect))
    if not marked and DESTROY_CONTROLLED_ARTIFACT_TOKEN_CAPABILITY not in program.capability_dependencies:
        return ()
    required = controlled_artifact_token_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if (not required or marked != tuple(program.effects[1:]) or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Controlled-artifact tokens require the original direct destruction spell and target")
    return required


DESTROY_CONTROLLED_ARTIFACT_DRAW_CAPABILITY = "resolution.effect_program.destroy_optional_controlled_artifact_draw"
DESTROY_CONTROLLED_ARTIFACT_DRAW_VERSION = 37
_OPTIONAL_TARGET_CONTROLLER_EQUALS_RESOLVER = "original_optional_target_controller_equals_resolver"
_OPTIONAL_TARGET_SCHEMA = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "up_to": 1, "types_any": ["artifact"],
})
_OPTIONAL_DESTROY = FrozenMap({"op": "destroy_targets", "cards": "$targets", "maximum_targets": 1})
_SOURCE_ATTACK = FrozenMap({"field": "card", "op": "eq", "value": "$source.ref"})


def controlled_artifact_draw_branch(draw):
    return {
        "op": _CONDITION_OPERATION, "schema_version": DESTROY_CONTROLLED_ARTIFACT_DRAW_VERSION,
        "player": "$controller",
        "condition": {"kind": _OPTIONAL_TARGET_CONTROLLER_EQUALS_RESOLVER, "reference": target_controller_reference()},
        "effects": [draw],
    }


def _uses_controlled_artifact_draw_branch(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != _CONDITION_OPERATION:
        return False
    condition = effect.get("condition")
    return (effect.get("schema_version") == DESTROY_CONTROLLED_ARTIFACT_DRAW_VERSION
            or isinstance(condition, Mapping) and condition.get("kind") == _OPTIONAL_TARGET_CONTROLLER_EQUALS_RESOLVER)


def uses_controlled_artifact_result_branch(effect):
    return uses_controlled_artifact_token_branch(effect) or _uses_controlled_artifact_draw_branch(effect)


def _draw_branch_parts(effect, *, actor=None):
    player = "$controller" if actor is None else actor
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "condition", "effects"}
            or effect["op"] != _CONDITION_OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != DESTROY_CONTROLLED_ARTIFACT_DRAW_VERSION
            or effect["player"] != player
            or not isinstance(effect["effects"], (list, tuple)) or len(effect["effects"]) != 1
            or effect["effects"][0] != {"op": "draw", "player": player, "count": 1, "private": True}
            or type(effect["effects"][0]["count"]) is not int or effect["effects"][0]["private"] is not True):
        raise ValueError("The optional artifact result requires one closed controller draw")
    condition = effect["condition"]
    if (not isinstance(condition, Mapping) or set(condition) != {"kind", "reference"}
            or condition["kind"] != _OPTIONAL_TARGET_CONTROLLER_EQUALS_RESOLVER):
        raise ValueError("The optional artifact draw requires the original target controller comparison")
    reference = condition["reference"]
    if actor is None:
        legal_reference = (isinstance(reference, Mapping) and type(reference.get("schema_version")) is int
                           and FrozenMap(reference) == FrozenMap(target_controller_reference()))
    else:
        legal_reference = reference is None or type(reference) is str and bool(reference) and not reference.startswith("$")
    if not legal_reference:
        raise ValueError("The optional artifact draw lost the original target controller")
    return reference, effect["effects"][0]


def controlled_artifact_result_effects(effect, *, actor, stack_controller):
    if not _uses_controlled_artifact_draw_branch(effect):
        return controlled_artifact_token_effects(effect, actor=actor, stack_controller=stack_controller)
    if type(actor) is not str or not actor or actor.startswith("$") or actor != stack_controller:
        raise ValueError("The original resolving controller owns the conditional draw")
    controller, draw = _draw_branch_parts(effect, actor=actor)
    return (FrozenMap(draw),) if controller == actor else ()


def controlled_artifact_draw_node_capabilities(*, effects, target_schema, cost_schema=None):
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities

    if (cost_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping)
            or FrozenMap(effects[0]) != _OPTIONAL_DESTROY or type(effects[0].get("maximum_targets")) is not int
            or not isinstance(target_schema, Mapping) or type(target_schema.get("up_to")) is not int
            or FrozenMap(target_schema) != _OPTIONAL_TARGET_SCHEMA):
        return ()
    try:
        _draw_branch_parts(effects[1])
    except (TypeError, ValueError):
        return ()
    destruction = fixed_homogeneous_target_set_node_capabilities(
        effects=effects[:1], target_schema=target_schema, mechanic_ids=("fixed-homogeneous-target-set", "cr-115-targets", "destroy"),
    )
    return tuple(sorted({DESTROY_CONTROLLED_ARTIFACT_DRAW_CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY,
        "resolution.effect.public_condition", "resolution.effect_program.bound_references",
        "resolution.effect_program.closed_components", "zone.draw.library_to_hand", *destruction})) if destruction else ()


def controlled_artifact_result_node_capabilities(*, effects, target_schema, cost_schema=None):
    derive = (controlled_artifact_draw_node_capabilities if any(_uses_controlled_artifact_draw_branch(e) for e in effects)
              else controlled_artifact_token_node_capabilities)
    return derive(effects=effects, target_schema=target_schema, cost_schema=cost_schema)


def controlled_artifact_result_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if _uses_controlled_artifact_draw_branch(effect))
    if not marked and DESTROY_CONTROLLED_ARTIFACT_DRAW_CAPABILITY not in program.capability_dependencies:
        return controlled_artifact_token_program_dependencies(program)
    required = controlled_artifact_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if (not required or marked != tuple(program.effects[1:]) or program.handlers
            or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or not program.ability_id.startswith("trigger:")
            or not isinstance(program.event_condition, Mapping) or FrozenMap(program.event_condition) != _SOURCE_ATTACK):
        raise ValueError("The optional artifact draw requires its original source-attack trigger and target")
    return required
