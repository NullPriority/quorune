"""Infer cleanup authority independently of descriptive coverage annotations."""
from collections.abc import Mapping
from ..created_token_cleanup_model import CreatedTokenCleanupSpec, TOKEN_CLEANUP_CAPABILITY, TOKEN_CLEANUP_MECHANIC
from ..fixed_token_production import CREATED_TOKEN_HASTE_FIELD


FIXED_HASTY_TOKEN_CLEANUP_CAPABILITY = "token.creation.fixed_haste_then_cleanup"


def created_token_cleanup_payload_dependencies(value):
    if isinstance(value, Mapping) and value.get('op') in {'create_token', 'choose_fixed_attacking_token_destinations'} and 'token_cleanup' in value:
        CreatedTokenCleanupSpec.from_dict(value['token_cleanup'])
        return (TOKEN_CLEANUP_CAPABILITY,)
    return ()


def created_token_cleanup_closure(effect):
    from .token_creation_capability_shapes import _fixed_token_effect_closure, created_token_haste_closure
    from ..replacement.immutable import FrozenMap, thaw_value

    spec = CreatedTokenCleanupSpec.from_dict(effect.get('token_cleanup'))
    base = {key: value for key, value in thaw_value(FrozenMap(effect)).items() if key != 'token_cleanup'}
    if CREATED_TOKEN_HASTE_FIELD in base:
        if type(base[CREATED_TOKEN_HASTE_FIELD]) is not int or base[CREATED_TOKEN_HASTE_FIELD] != 1:
            raise ValueError("An existing token cleanup requires its original haste grant")
        required, mechanics = created_token_haste_closure(base)
        if spec != CreatedTokenCleanupSpec("sacrifice", "next_end_step") or base["quantity"] < 2:
            raise ValueError("A fixed plural hasty token batch may schedule only next-end-step sacrifice")
        return (*required, TOKEN_CLEANUP_CAPABILITY, FIXED_HASTY_TOKEN_CLEANUP_CAPABILITY), mechanics
    closed = _fixed_token_effect_closure(base)
    if closed is None:
        raise ValueError('Token cleanup requires an independently closed fixed definition')
    return tuple(sorted({TOKEN_CLEANUP_CAPABILITY, *closed[0]})), closed[1]


def created_token_cleanup_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1 or effects[0].get('op') != 'create_token'
            or TOKEN_CLEANUP_MECHANIC not in mechanic_ids):
        return ()
    try:
        required, mechanics = created_token_cleanup_closure(effects[0])
        return required if mechanics.issubset(set(mechanic_ids)) else ()
    except (TypeError, ValueError, KeyError):
        return ()
