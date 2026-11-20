"""Exact clauses that consume the creature count of a targeted graveyard exile."""

from . import regex_working_set as re
from ..targeted_graveyard_outcome_model import (
    CreatureExileOutcome,
    GraveyardExileTargetScope,
    TargetedGraveyardExileOutcomeSpec,
)


_SINGLE_OPTIONAL = r'Exile up to one target card from a graveyard\. '
_TWO_ACROSS = r'Exile up to two target cards from graveyards\. '
_TWO_TOGETHER = r'Exile up to two target cards from a single graveyard\. '
_DRAIN = r'each opponent loses 2 life and you gain 2 life\.'
_CLUE_REMINDER = r'''(?: \(It['’]s an artifact with "\{2\}, Sacrifice this token: Draw a card\."\))?'''

_PATTERNS = (
    (_SINGLE_OPTIONAL + r'If a creature card was exiled this way, ' + _DRAIN,
        GraveyardExileTargetScope.UP_TO_ONE, CreatureExileOutcome.DRAIN_TWO_ONCE),
    (_TWO_TOGETHER + r'If at least one creature card was exiled this way, ' + _DRAIN,
        GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD, CreatureExileOutcome.DRAIN_TWO_ONCE),
    (_TWO_ACROSS + r'(?:You gain 1 life for each creature card exiled this way|'
        r'For each creature card exiled this way, you gain 1 life)\.',
        GraveyardExileTargetScope.UP_TO_TWO, CreatureExileOutcome.GAIN_ONE_PER_CREATURE),
    (_SINGLE_OPTIONAL + r'If a creature card is exiled this way, create a Clue token\.' + _CLUE_REMINDER,
        GraveyardExileTargetScope.UP_TO_ONE, CreatureExileOutcome.CREATE_ONE_CLUE),
    (r'Exile target card from a graveyard\. If a creature card was exiled this way, create a Food token\.',
        GraveyardExileTargetScope.ONE, CreatureExileOutcome.CREATE_ONE_FOOD),
)


def parse_targeted_graveyard_exile_outcome(text: str) -> TargetedGraveyardExileOutcomeSpec | None:
    instruction = text.strip()
    for pattern, scope, outcome in _PATTERNS:
        if re.fullmatch(pattern, instruction, re.IGNORECASE):
            return TargetedGraveyardExileOutcomeSpec(scope, outcome)
    return None


def targeted_graveyard_exile_outcome_template(text: str):
    recipe = parse_targeted_graveyard_exile_outcome(text)
    if recipe is None:
        return None

    from ..rules.targeted_graveyard_outcomes import targeted_graveyard_outcome_effect, targeted_graveyard_outcome_schema

    return (
        'targeted-graveyard-exile-outcome-v1',
        (targeted_graveyard_outcome_effect(recipe),),
        targeted_graveyard_outcome_schema(recipe),
        ('exile', 'fixed-public-zone-move', 'cr-115-targets', 'fixed-homogeneous-target-set'),
    )
