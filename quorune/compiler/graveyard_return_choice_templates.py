"""Optional untargeted returns; mill sequencing remains in the clause owner."""
import re

from ..rules.graveyard_card_targets import GraveyardCardTargetKind
from ..rules.graveyard_return_choices import GraveyardReturnSelection, OPERATION, MECHANIC


def _single_return_choice(text):
    match = re.fullmatch(r'You may return (?:a|one) (?P<subject>creature or land|creature|land|permanent) '
        r'card from your graveyard to your hand\.?', text.strip(), re.IGNORECASE)
    if match is None:
        return None
    subject = match['subject'].casefold()
    types = ('creature', 'land') if subject == 'creature or land' else GraveyardCardTargetKind(subject+' card').types_any
    return ('own-graveyard-return-choice-v1', ({'op': OPERATION, 'player': '$controller',
        'selection': GraveyardReturnSelection(types).to_dict()},), None, (MECHANIC,))


def graveyard_return_choice_template(text):
    returned = _single_return_choice(text)
    if returned is not None:
        return returned
    match = re.fullmatch(r'(?P<mill>Mill .+? cards?)(?:, then|\. Then) (?P<returned>You may return .+)',
        text.strip(), re.IGNORECASE)
    if match is None:
        return None
    from .mill_templates import fixed_mill_effect_template
    from .fixed_effect_clause_sequences import FixedEffectClauseSequenceTemplate, FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
    mill = fixed_mill_effect_template(match['mill']+'.')
    returned = _single_return_choice(match['returned'])
    if mill is None or returned is None or mill.target_schema is not None:
        return None
    first = mill.compiled()
    return FixedEffectClauseSequenceTemplate(component_template_ids=(first[0], returned[0]),
        _effects=(*first[1], *returned[1]), _target_schema=None,
        mechanic_ids=(FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC, *first[3], *returned[3])).compiled()
