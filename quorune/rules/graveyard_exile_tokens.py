"""Bind the exact announced target and commit its canonical token result."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..graveyard_exile_token_model import (
    GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES,
    GRAVEYARD_EXILE_TOKEN_OPERATION,
    GraveyardExileTokenSpec,
)
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_followup_capabilities
from .effect_shape_walkers import nested_effect_operations
from .graveyard_exile_branches import commit_classified_graveyard_exile, graveyard_exile_branch_target_schema


def _is_graveyard_exile_token(effect) -> bool:
    return isinstance(effect, Mapping) and effect.get('op') == GRAVEYARD_EXILE_TOKEN_OPERATION


def graveyard_exile_token_payload_dependencies(effect) -> tuple[str, ...]:
    if not _is_graveyard_exile_token(effect):
        return ()
    if set(effect) != {'op', 'card', 'result'} or effect['card'] != '$target.0':
        raise ValueError('Graveyard exile token requires exactly one direct card target')
    spec = GraveyardExileTokenSpec.from_dict(effect['result'])
    token_dependencies = fixed_followup_capabilities(spec.token_effect, allow_typed_token_ability=True)
    return (*GRAVEYARD_EXILE_TOKEN_BASE_DEPENDENCIES, *token_dependencies)


def graveyard_exile_token_node_capabilities(*, effects, target_schema, **kwargs) -> tuple[str, ...]:
    if (len(effects) != 1 or not _is_graveyard_exile_token(effects[0])
            or not isinstance(target_schema, Mapping) or type(target_schema.get('count')) is not int
            or FrozenMap(target_schema) != FrozenMap(graveyard_exile_branch_target_schema())):
        return ()
    try:
        return graveyard_exile_token_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def graveyard_exile_token_runtime_dependencies(program) -> tuple[str, ...]:
    if GRAVEYARD_EXILE_TOKEN_OPERATION not in nested_effect_operations((program.effects, program.handlers)):
        return ()
    dependencies = graveyard_exile_token_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if not dependencies or GRAVEYARD_EXILE_TOKEN_OPERATION in nested_effect_operations(program.handlers):
        raise ValueError('Graveyard exile token needs its one direct instruction and exact target schema')
    return dependencies


def is_closed_graveyard_exile_token_activation(program) -> bool:
    if program.event != 'activate' or len(program.handlers) != 1:
        return False
    dependencies = graveyard_exile_token_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if not dependencies or not set(dependencies) <= set(program.capability_dependencies):
        return False
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    try:
        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    except (TypeError, ValueError, KeyError):
        return False
    return ability.builtin_semantic_key == program.key and ability.target_schema == FrozenMap(program.target_schema)


def commit_graveyard_exile_token(host, intent) -> CommittedQuantityFollowup:
    from ..semantic_runtime.graveyard_exile_token_intent import GraveyardExileTokenIntent

    if not isinstance(intent, GraveyardExileTokenIntent):
        raise GameRuleError('Graveyard exile token requires a typed intent')
    recipe = intent.result
    facts = commit_classified_graveyard_exile(host, intent, requires_committed_exile=recipe.requires_committed_exile)
    creates = recipe.should_create(exile_committed=facts.committed, creature_card=facts.creature_card)
    return CommittedQuantityFollowup((recipe.token_effect,) if creates else ())
