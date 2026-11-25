"""Closed untargeted returns from the controller's graveyard."""
from . import regex_working_set as re

from ..creature_subtypes import canonical_creature_subtype
from ..rules.graveyard_card_targets import GraveyardCardTargetKind
from ..rules.graveyard_return_choices import GraveyardReturnSelection, OPERATION, MECHANIC


def _qualified_return_choice(text):
    match = re.fullmatch(
        r'You may return a non-(?P<subtype>.+?) creature card or a planeswalker card '
        r'from your graveyard to your hand\.?', text.strip(), re.IGNORECASE,
    )
    subtype = canonical_creature_subtype(match['subtype']) if match else None
    if subtype is None:
        return None
    selection = GraveyardReturnSelection(('creature', 'planeswalker'), schema_version=3,
                                         excluded_creature_subtype=subtype)
    return ('own-graveyard-subtype-qualified-return-v3',
            ({'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()},), None, (MECHANIC,))


def _single_return_choice(text):
    match = re.fullmatch(r'You may return (?:a|one) (?P<subject>creature or land|creature|land|permanent) '
        r'card from your graveyard to your hand\.?', text.strip(), re.IGNORECASE)
    if match is None:
        return _qualified_return_choice(text)
    subject = match['subject'].casefold()
    types = ('creature', 'land') if subject == 'creature or land' else GraveyardCardTargetKind(subject+' card').types_any
    return ('own-graveyard-return-choice-v1', ({'op': OPERATION, 'player': '$controller',
        'selection': GraveyardReturnSelection(types).to_dict()},), None, (MECHANIC,))


def _greatest_power_return_choice(text):
    printed = (
        'return to your hand the creature card in your graveyard with the greatest power. '
        'if two or more cards are tied for greatest power, you choose one of them.'
    )
    if ' '.join(text.casefold().split()) != printed:
        return None
    selection = GraveyardReturnSelection(('creature',), schema_version=2)
    return ('own-graveyard-greatest-power-return-v2',
            ({'op': OPERATION, 'player': '$controller', 'selection': selection.to_dict()},), None, (MECHANIC,))


def graveyard_return_choice_template(text):
    returned = _greatest_power_return_choice(text) or _single_return_choice(text)
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
