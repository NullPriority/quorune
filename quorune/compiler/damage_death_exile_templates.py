from __future__ import annotations

"""Retain the exact death-replacement recipient named by a damage instruction."""

import re

from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_FIELD, DAMAGE_DEATH_EXILE_MECHANIC, DamageDeathExileScope
from ..rules.damage_capability_shapes import damage_death_exile_scope_is_closed


_FOLLOWUP = re.compile(
    r'(?P<body>.+\.) If (?P<subject>that creature|(?:a|any) creature dealt damage this way) '
    r'would die this turn, exile it instead\.', re.I,
)


def damage_death_exile_template(text, *, compile_body):
    match = _FOLLOWUP.fullmatch(text.strip())
    if match is None:
        return None
    template, effects, schema, mechanics = compile_body(match['body'])
    if template is None or not effects or effects[0].get('op') not in {'damage', 'damage_fixed_set'}:
        return None
    # These intervening instructions cannot move an affected object before SBA.
    if any(effect.get('op') not in {'mana', 'apply_source_characteristics_until_end_of_turn'} for effect in effects[1:]):
        return None
    scope = (DamageDeathExileScope.TARGET if match['subject'].casefold() == 'that creature'
             else DamageDeathExileScope.DEALT_CREATURES)
    damage = {**effects[0], DAMAGE_DEATH_EXILE_FIELD: scope.value}
    if not damage_death_exile_scope_is_closed(damage, schema):
        return None
    return f'damage-death-exile-{template}', (damage, *effects[1:]), schema, (*mechanics, DAMAGE_DEATH_EXILE_MECHANIC)
