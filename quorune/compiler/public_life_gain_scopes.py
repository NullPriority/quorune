"""Closed fixed public-recipient life gain through the existing life owner."""
from __future__ import annotations
from . import regex_working_set as re
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..rules.public_life_gain_scopes import PUBLIC_LIFE_GAIN_FIELD, PUBLIC_LIFE_GAIN_MECHANIC


def fixed_public_life_gain_template(text):
    from .life_templates import FixedLifeEffectTemplate, _player_target_schema
    match=re.fullmatch(rf"(?P<who>each player|each opponent|target player|target opponent) gains (?P<count>{FIXED_COUNT_PATTERN}) life\.?",text,re.I)
    if match is None:return None
    amount=fixed_number(match['count'])
    if amount<1:return None
    who=match['who'].casefold()
    if who.startswith('target '):
        relation='opponent' if who=='target opponent' else 'any'
        return FixedLifeEffectTemplate('gain-life-'+who.replace(' ','-')+'-v1',
            {'op':'life','player':'$target.0','delta':amount},_player_target_schema(relation),
            ('cr-119-life','cr-115-targets'))
    scope='all_active_players' if who=='each player' else 'controller_opponents'
    return FixedLifeEffectTemplate('gain-life-'+scope.replace('_','-')+'-v1',
        {'op':'life',PUBLIC_LIFE_GAIN_FIELD:1,'scope':scope,'delta':amount},
        mechanic_ids=('cr-119-life','cr-101-the-magic-golden-rules',PUBLIC_LIFE_GAIN_MECHANIC))
