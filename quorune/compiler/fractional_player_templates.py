from __future__ import annotations

"""Closed half-life and affected-domain instructions over existing owners."""

from dataclasses import dataclass
import re

from ..fractional_quantity_model import HalfQuantitySpec, HALF_LIFE_OPERATION, HALF_LIFE_MECHANIC, HALF_CHOICE_MECHANIC
from .life_templates import fixed_life_effect_template, _player_target_schema
from .affected_player_discard_templates import FixedAffectedPlayerDiscardTemplate, AffectedPlayerDiscardSubject
from .affected_player_sacrifice_templates import FixedAffectedPlayerSacrificeTemplate, AffectedPlayerSacrificeSubject, _predicate


@dataclass(frozen=True, slots=True)
class FractionalPlayerTemplate:
    template_id: str
    effects: tuple
    target_schema: object
    mechanics: tuple

    def compiled(self):
        return self.template_id, self.effects, self.target_schema, self.mechanics


def fractional_player_result_template(text):
    normalized = ' '.join(text.strip().split())
    match = re.fullmatch(r'(?P<subject>You|Target player|Target opponent|Each player|Each opponent) '
        r'(?P<body>lose[s]? half (?:your|their) life|discards half the cards in their hand|'
        r'sacrifices half the creatures they control of their choice), rounded (?P<rounding>up|down)\.', normalized, re.IGNORECASE)
    if match is None:
        return None
    subject = match['subject'].casefold()
    body = match['body'].casefold()
    fraction = HalfQuantitySpec(match['rounding'].casefold())
    targeted = subject.startswith('target ')
    if body.startswith('lose'):
        if subject == 'you' and not body.startswith('lose half your life') or subject != 'you' and not body.startswith('loses half their life'):
            return None
        effect = {'op': HALF_LIFE_OPERATION, 'amount': fraction.to_dict()}
        if subject in {'each player', 'each opponent'}:
            effect['scope'] = 'all_active_players' if subject == 'each player' else 'opponents'
        else:
            effect['player'] = '$target.0' if targeted else '$controller'
        schema = _player_target_schema('opponent' if subject == 'target opponent' else 'any') if targeted else None
        return FractionalPlayerTemplate('half-player-life-v1', (effect,), schema,
            (HALF_LIFE_MECHANIC, 'cr-119-life', *(('cr-115-targets',) if targeted else ())))
    if subject == 'you':
        return None
    subjects = {'target player': 'target_player', 'target opponent': 'target_opponent',
                'each player': 'each_player', 'each opponent': 'each_opponent'}
    if body.startswith('discards'):
        base = FixedAffectedPlayerDiscardTemplate(AffectedPlayerDiscardSubject(subjects[subject]), 1)
    else:
        base = FixedAffectedPlayerSacrificeTemplate(AffectedPlayerSacrificeSubject(subjects[subject]), 1, _predicate('creature', count=1))
    effect = dict(base.effects[0])
    effect['count'] = fraction.to_dict()
    return FractionalPlayerTemplate('half-affected-player-domain-v1', (effect,), base.target_schema,
        (*base.mechanics, HALF_CHOICE_MECHANIC))


def fixed_player_result_template(text):
    if 'half' in text.casefold():
        fractional = fractional_player_result_template(text)
        if fractional is not None:
            return fractional
    return fixed_life_effect_template(text)
