"""Whole-clause graveyard recycling through existing zone and shuffle owners."""
from dataclasses import dataclass
from . import regex_working_set as re
from ..graveyard_recycling_model import GraveyardRecyclingSpec, GRAVEYARD_RECYCLING_OPERATION


@dataclass(frozen=True, slots=True)
class GraveyardRecyclingTemplate:
    spec: GraveyardRecyclingSpec

    def __post_init__(self):
        if not isinstance(self.spec, GraveyardRecyclingSpec):
            raise ValueError('Graveyard recycling requires its typed complete instruction')

    def compiled(self):
        targeted = self.spec.scope == 'target_player'
        player = {'controller': '$controller', 'target_player': '$target.0', 'all_players': 'all'}[self.spec.scope]
        schema = {'zones': ['player'], 'categories': ['player'], 'player_relation': 'any', 'count': 1} if targeted else None
        template = ('whole-hand-graveyard-recycling-'+self.spec.scope+'-v2'
            if self.spec.include_hand else 'whole-graveyard-recycling-'+self.spec.scope+'-v1')
        return (template,
            ({'op': GRAVEYARD_RECYCLING_OPERATION, 'recycling': self.spec.to_dict(), 'player': player},),
            schema, ('fixed-owner-zone-move', *(('cr-115-targets',) if targeted else ())))


def graveyard_recycling_template(text):
    normalized = ' '.join(text.strip().split())
    hand = re.fullmatch(r'Each player shuffles their hand and graveyard into their library\.?', normalized, re.I)
    if hand is not None:
        return GraveyardRecyclingTemplate(GraveyardRecyclingSpec('all_players',2))
    if re.fullmatch(r'Shuffle your hand and graveyard into your library\.?', normalized, re.I):
        return GraveyardRecyclingTemplate(GraveyardRecyclingSpec('controller',2))
    match = re.fullmatch(r'(?P<subject>Target player|Each player) shuffles their graveyard into their library\.?', normalized, re.I)
    if match is not None:
        scope = 'target_player' if match['subject'].casefold() == 'target player' else 'all_players'
        return GraveyardRecyclingTemplate(GraveyardRecyclingSpec(scope))
    if re.fullmatch(r'Shuffle your graveyard into your library\.?', normalized, re.I):
        return GraveyardRecyclingTemplate(GraveyardRecyclingSpec('controller'))
    return None
