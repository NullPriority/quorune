from __future__ import annotations

"""Closed half-life and affected-domain instructions over existing owners."""

from dataclasses import dataclass
import re

from ..fractional_quantity_model import HalfQuantitySpec, HALF_LIFE_OPERATION, HALF_LIFE_MECHANIC, HALF_CHOICE_MECHANIC
from .closed_effect_programs import closed_effect_program_template
from .life_templates import fixed_life_effect_template, _player_target_schema
from .affected_player_discard_templates import FixedAffectedPlayerDiscardTemplate, AffectedPlayerDiscardSubject
from .affected_player_sacrifice_templates import FixedAffectedPlayerSacrificeTemplate, AffectedPlayerSacrificeSubject, _predicate


_SACRIFICE_DOMAIN = r"(?:creatures|(?:nonland )?permanents|non-[a-z][a-z -]* creatures)"
_SHARED_ROUNDING_SEQUENCE = re.compile(
    r"(?P<subject>Each player|Each opponent) loses half their life, then discards half the cards "
    rf"in their hand, then sacrifices half the (?P<domain>{_SACRIFICE_DOMAIN}) they control "
    r"of their choice\. Round (?P<rounding>up|down) each time\.",
    re.IGNORECASE,
)


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
        rf'sacrifices half the (?P<domain>{_SACRIFICE_DOMAIN}) they control of their choice), rounded (?P<rounding>up|down)\.',
        normalized, re.IGNORECASE)
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
        predicate = _predicate(match["domain"].casefold().removesuffix("s"), count=1)
        if predicate is None:
            return None
        base = FixedAffectedPlayerSacrificeTemplate(AffectedPlayerSacrificeSubject(subjects[subject]), 1, predicate)
    effect = dict(base.effects[0])
    effect['count'] = fraction.to_dict()
    return FractionalPlayerTemplate('half-affected-player-domain-v1', (effect,), base.target_schema,
        (*base.mechanics, HALF_CHOICE_MECHANIC))


def fractional_player_sequence_template(text):
    match = _SHARED_ROUNDING_SEQUENCE.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    subject, domain, rounding = match["subject"], match["domain"], match["rounding"]
    clauses = (
        "loses half their life",
        "discards half the cards in their hand",
        f"sacrifices half the {domain} they control of their choice",
    )
    normalized = " ".join(f"{subject} {clause}, rounded {rounding}." for clause in clauses)

    def compile_component(clause):
        result = fractional_player_result_template(clause)
        return result.compiled() if result is not None else (None, (), None, ())

    return closed_effect_program_template(normalized, compile_component=compile_component)


def fixed_player_result_template(text,*,source_name=None):
    if 'half' in text.casefold():
        fractional = fractional_player_result_template(text) or fractional_player_sequence_template(text)
        if fractional is not None:
            return fractional
    return fixed_life_effect_template(text,source_name=source_name)
