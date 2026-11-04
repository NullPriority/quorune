"""Closed fixed-definition token results entering combat through shared authority."""
from collections.abc import Mapping

ATTACKING_TOKEN_OPERATION = 'choose_fixed_attacking_token_destinations'
ATTACKING_TOKEN_CAPABILITY = 'token.creation.fixed_attacking_definition'
ATTACKING_TOKEN_MECHANIC = 'fixed-attacking-token-result'


def attacking_token_result_closure(effect):
    from .token_creation_capability_shapes import _fixed_token_effect_closure
    from ..replacement.immutable import FrozenMap, thaw_value
    if (not isinstance(effect, Mapping) or set(effect) != {
            'op', 'player', 'quantity', 'name', 'characteristics', 'tapped', 'token_entry_version'}
            or effect.get('op') != ATTACKING_TOKEN_OPERATION
            or effect.get('player') != '$controller'
            or type(effect.get('token_entry_version')) is not int or effect['token_entry_version'] != 1
            or type(effect.get('tapped')) is not bool):
        raise ValueError('Fixed attacking token result is malformed')
    effect = thaw_value(FrozenMap(effect))
    fixed = {'op': 'create_token', 'controller': '$controller', 'name': effect['name'],
        'quantity': effect['quantity'], 'characteristics': effect['characteristics'],
        **({'tapped': True} if effect['tapped'] else {})}
    closed = _fixed_token_effect_closure(fixed)
    if closed is None or 'creature' not in str(effect['characteristics'].get('type_line', '')).casefold().split(' — ')[0].split():
        raise ValueError('Attacking token requires an independently closed creature definition')
    return tuple(sorted({ATTACKING_TOKEN_CAPABILITY, *closed[0]})), closed[1]


def attacking_token_payload_dependencies(value):
    if isinstance(value, Mapping) and value.get('op') == ATTACKING_TOKEN_OPERATION:
        return attacking_token_result_closure(value)[0]
    return ()


def attacking_token_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or ATTACKING_TOKEN_MECHANIC not in mechanic_ids:
        return ()
    try:
        required, mechanics = attacking_token_result_closure(effects[0])
        return required if mechanics.issubset(set(mechanic_ids)) else ()
    except (TypeError, ValueError, KeyError):
        return ()
