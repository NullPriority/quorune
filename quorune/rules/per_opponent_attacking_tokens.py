"""Explicit opponent groups over the existing fixed attacking-token definition."""
from collections.abc import Mapping

from ..attacking_opponent_model import AttackingOpponentScope
from .attacking_token_results import ATTACKING_TOKEN_OPERATION, attacking_token_result_closure

OPPONENT_TOKEN_SCOPE_FIELD = 'opponent_scope'
OPPONENT_ATTACKING_TOKEN_CAPABILITY = 'token.creation.fixed_per_opponent_attacking_definition'
OPPONENT_ATTACKING_TOKEN_MECHANIC = 'fixed-per-opponent-attacking-token-result'


def per_opponent_attacking_token_closure(effect):
    expected = {'op', 'player', 'quantity', 'name', 'characteristics', 'tapped',
        'token_entry_version', OPPONENT_TOKEN_SCOPE_FIELD}
    if (not isinstance(effect, Mapping) or set(effect) != expected
            or effect.get('op') != ATTACKING_TOKEN_OPERATION
            or effect.get('player') != '$controller'
            or type(effect.get('token_entry_version')) is not int or effect['token_entry_version'] != 3
            or effect.get('tapped') is not True
            or type(effect.get('quantity')) is not int or effect['quantity'] < 0):
        raise ValueError('Per-opponent attacking tokens require an explicit fixed version-three result')
    AttackingOpponentScope.from_dict(effect[OPPONENT_TOKEN_SCOPE_FIELD])
    definition = {key: value for key, value in effect.items() if key != OPPONENT_TOKEN_SCOPE_FIELD}
    definition['token_entry_version'] = 1
    capabilities, mechanics = attacking_token_result_closure(definition)
    return tuple(sorted({*capabilities, OPPONENT_ATTACKING_TOKEN_CAPABILITY})), frozenset(
        {*mechanics, OPPONENT_ATTACKING_TOKEN_MECHANIC})


def per_opponent_token_inputs(actor, effect, *, seats, active_seats, attacking):
    """Keep one original opponent group per printed token through replacements."""
    public = {key: value for key, value in effect.items() if not key.startswith('_')}
    if public.get('player') != actor or actor not in active_seats:
        raise ValueError('Per-opponent token creation requires its active resolving controller')
    per_opponent_attacking_token_closure({**public, 'player': '$controller'})
    opponents = effect.get('_opponents', tuple(seat for seat in active_seats if seat != actor))
    if (not isinstance(opponents, (tuple, list)) or len(set(opponents)) != len(opponents)
            or any(type(seat) is not str or seat not in seats or seat == actor for seat in opponents)):
        raise ValueError('Per-opponent tokens require their original known opponent groups')
    opponents = tuple(opponents)
    groups = tuple(seat for seat in opponents for _ in range(effect['quantity']))
    return opponents, groups, {
        'name': effect['name'], 'quantity': len(groups), 'tapped': True,
        'characteristics': effect['characteristics'],
        'attacking_groups': groups if attacking else (),
    }
