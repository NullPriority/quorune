"""Closed fixed-definition token results entering combat through shared authority."""
from collections.abc import Mapping

ATTACKING_TOKEN_OPERATION = 'choose_fixed_attacking_token_destinations'
ATTACKING_TOKEN_CAPABILITY = 'token.creation.fixed_attacking_definition'
ATTACKING_TOKEN_MECHANIC = 'fixed-attacking-token-result'


def attacking_token_result_closure(effect):
    from .token_creation_capability_shapes import _fixed_token_effect_closure
    from ..replacement.immutable import FrozenMap, thaw_value
    version = effect.get('token_entry_version') if isinstance(effect, Mapping) else None
    from .source_counter_attacking_tokens import source_counter_attacking_token_closure, uses_source_counter_attacking_tokens

    if uses_source_counter_attacking_tokens(effect):
        return source_counter_attacking_token_closure(effect)
    from .attacking_entry_copy import DEPENDENCIES, uses_copy, validate_compiled
    if isinstance(effect, Mapping) and uses_copy(effect):
        validate_compiled(effect)
        return DEPENDENCIES, {"cr-111-tokens", "cr-115-targets"}
    if type(version) is int and version == 3:
        from .per_opponent_attacking_tokens import per_opponent_attacking_token_closure
        return per_opponent_attacking_token_closure(effect)
    expected = {'op', 'player', 'quantity', 'name', 'characteristics', 'tapped', 'token_entry_version'} | ({'token_cleanup'} if version == 2 else set())
    if (not isinstance(effect, Mapping) or set(effect) != expected
            or effect.get('op') != ATTACKING_TOKEN_OPERATION
            or effect.get('player') != '$controller'
            or type(version) is not int or version not in {1, 2}
            or type(effect.get('tapped')) is not bool):
        raise ValueError('Fixed attacking token result is malformed')
    effect = thaw_value(FrozenMap(effect))
    fixed = {'op': 'create_token', 'controller': '$controller', 'name': effect['name'],
        'quantity': effect['quantity'], 'characteristics': effect['characteristics'],
        **({'tapped': True} if effect['tapped'] else {})}
    closed = _fixed_token_effect_closure(fixed)
    if closed is None or 'creature' not in str(effect['characteristics'].get('type_line', '')).casefold().split(' — ')[0].split():
        raise ValueError('Attacking token requires an independently closed creature definition')
    from ..created_token_cleanup_model import CreatedTokenCleanupSpec, TOKEN_CLEANUP_CAPABILITY
    if version == 2: CreatedTokenCleanupSpec.from_dict(effect['token_cleanup'])
    return tuple(sorted({ATTACKING_TOKEN_CAPABILITY, *closed[0], *({TOKEN_CLEANUP_CAPABILITY} if version == 2 else set())})), closed[1]


def attacking_token_payload_dependencies(value):
    if isinstance(value, Mapping) and value.get('op') == ATTACKING_TOKEN_OPERATION:
        return attacking_token_result_closure(value)[0]
    return ()


def attacking_token_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .attacking_entry_copy import target_schema as attacking_copy_target_schema, uses_copy
    from ..replacement.immutable import thaw_value

    if len(effects) != 1 or ATTACKING_TOKEN_MECHANIC not in mechanic_ids:
        return ()
    if uses_copy(effects[0]):
        if thaw_value(target_schema) != attacking_copy_target_schema():
            return ()
    elif target_schema is not None:
        return ()
    try:
        required, mechanics = attacking_token_result_closure(effects[0])
        return required if mechanics.issubset(set(mechanic_ids)) else ()
    except (TypeError, ValueError, KeyError):
        return ()
