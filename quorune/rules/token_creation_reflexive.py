"""Authorize token creation and an independently owned reflexive continuation."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..errors import GameRuleError
from ..fixed_token_production import fixed_token_effect_shape_is_closed
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_CAPABILITY, TOKEN_CREATION_REFLEXIVE_FIELD,
    REFLEXIVE_PAYMENT_MECHANIC, ReflexiveTriggerProgram,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.context import semantic_source_context
from .effect_shape_walkers import contains_nested_key
from .reflexive_payment_shapes import reflexive_program_capabilities
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities


def token_creation_reflexive_parts(effect):
    if not isinstance(effect, Mapping) or TOKEN_CREATION_REFLEXIVE_FIELD not in effect:
        raise ValueError("Token creation requires its typed reflexive result")
    creation = {key: value for key, value in effect.items() if key != TOKEN_CREATION_REFLEXIVE_FIELD}
    if not fixed_token_effect_shape_is_closed(creation):
        raise ValueError("A reflexive token result requires a fixed nontargeted token instruction")
    child = ReflexiveTriggerProgram.from_dict(effect[TOKEN_CREATION_REFLEXIVE_FIELD])
    if (child.schema_version != BOUND_REFLEXIVE_PROGRAM_VERSION or child.target_schema is None
            or len(child.effects) != 1
            or child.effects[0].get("op") not in {
                "tap", "untap", "exile_public_graveyard_targets", "modify_stats_until_end_of_turn",
            }):
        raise ValueError("Reflexive token creation requires one independently owned public target result")
    return creation, child


def token_creation_reflexive_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if len(effects) != 1 or target_schema is not None or REFLEXIVE_PAYMENT_MECHANIC not in mechanics:
        return ()
    try:
        creation, child = token_creation_reflexive_parts(effects[0])
        primary = fixed_token_creation_node_capabilities(effects=(creation,), target_schema=None,
            mechanic_ids=mechanics - {REFLEXIVE_PAYMENT_MECHANIC, CURRENT_ABILITY_FRAGMENT_COVERAGE})
        result = reflexive_program_capabilities(child)
    except (KeyError, TypeError, ValueError):
        return ()
    if not primary or not result:
        return ()
    return tuple(sorted({REFLEXIVE_PAYMENT_CAPABILITY, "trigger.placement.apnap", *primary, *result}))


def token_creation_reflexive_runtime_dependencies(program):
    marked = REFLEXIVE_PAYMENT_MECHANIC in program.coverage and any(
        effect.get("op") == "create_token" for effect in program.effects
    )
    if not (marked or contains_nested_key(program.effects, TOKEN_CREATION_REFLEXIVE_FIELD)):
        return ()
    if (program.event, program.active_zone) not in {("resolve", "stack"), ("permanent.enter.self", "battlefield")}:
        raise ValueError("Reflexive token creation requires a resolving spell or original battlefield entry trigger")
    required = token_creation_reflexive_node_capabilities(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    if not required:
        raise ValueError("Reflexive token creation requires the complete creation and public target owners")
    return required


def is_closed_token_creation_reflexive_program(program) -> bool:
    try:
        required = token_creation_reflexive_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def authorize_token_creation_reflexive(host, effect, *, actor):
    """Bind the exact creating effect before the ordinary token owner mutates state."""
    raw_source = effect.get("_runtime_source")
    ref = raw_source.get("stack_ref") if isinstance(raw_source, Mapping) else None
    parent = next((item for item in host.state.stack if item.ref == ref), None)
    if (parent is None or parent.controller != actor or actor not in host.active_seats
            or parent.kind not in {"spell", "spell_copy", "triggered_ability"}
            or parent.context.get("currently_resolving") is not True
            or raw_source != semantic_source_context(parent, host.state.cards).to_dict()):
        raise GameRuleError("Reflexive token creation requires the original resolving source and controller")
    program = host.semantics.get(parent.semantic_key)
    declared = {key: value for key, value in effect.items() if key not in {"_runtime_source", "_replacement_selections"}}
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or len(program.effects) != 1 or declared.get("controller") != actor):
        raise GameRuleError("Reflexive token creation requires its admitted unmodified program")
    declared["controller"] = "$controller"
    if FrozenMap(declared) != FrozenMap(program.effects[0]):
        raise GameRuleError("Reflexive token creation differs from its admitted instruction")
    try:
        required = token_creation_reflexive_runtime_dependencies(program)
        if not required or not set(required) <= set(program.capability_dependencies):
            raise ValueError("Reflexive token creation lost a required owner")
        _, child = token_creation_reflexive_parts(declared)
        from .reflexive_triggers import resolving_reflexive_source
        resolving_reflexive_source(host, parent, child)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return parent, child
