from __future__ import annotations

from . import regex_working_set as re

from ..cast_lifecycles import GRANTED_ESCAPE_CAPABILITY, granted_escape_descriptor
from ..object_predicate import ObjectQuerySpec
from ..rules.turn_cast_grant_model import (
    TURN_CAST_GRANT_CAPABILITY, TURN_CAST_GRANT_EVENT, TURN_CAST_GRANT_HANDLER, TurnCastGrantSpec,
    TURN_CAST_RIDER_HANDLERS, TurnCastGrantScope, permanent_type_graveyard_grant,
)

_CLAUSE = re.compile(r'^Once during each of your turns, you may cast (?P<subject>.+?) spell '
    r'from (?P<origin>your hand or the top of your library|your hand|your graveyard)'
    r'(?P<free> without paying its mana cost)?\.?$', re.I)


_COMMAND_GRAVEYARD_CAST = re.compile(
    r"You may cast instant and sorcery spells from your graveyard\. "
    r"If a spell cast this way would be put into your graveyard, exile it instead\.",
    re.IGNORECASE,
)
_GRANTED_ESCAPE = re.compile(
    r"Each nonland card in your graveyard has escape\. The escape cost is equal to the card['’]s mana cost "
    r"plus exile three other cards from your graveyard\."
    r"(?: \(You may cast cards from your graveyard for their escape cost\.\))?",
    re.IGNORECASE,
)


def command_graveyard_cast_grant_handler(text):
    if _COMMAND_GRAVEYARD_CAST.fullmatch(" ".join(text.strip().split())) is None:
        return None
    spec = TurnCastGrantSpec(
        origins=("graveyard",),
        predicates_any=(ObjectQuerySpec(types_any=("instant", "sorcery")),),
        cast_rider="graveyard_exile",
        source_scope=TurnCastGrantScope.COMMAND_UNLIMITED,
    )
    return (
        "command-graveyard-cast-grant-v1",
        {
            "handler_id": spec.handler_id, "schema_version": 1, "event": TURN_CAST_GRANT_EVENT,
            "requires_complete_card_program": True, "grant": spec.to_dict(),
        },
        spec.capability_dependencies,
    )


def _spell_predicate(subject):
    from .cast_cost_modifier_templates import fixed_spell_predicate
    from .creature_subtypes import canonical_creature_subtype
    if subject.endswith(' creature'):
        subtype = canonical_creature_subtype(subject[:-9])
        if subtype is not None:
            return ObjectQuerySpec(types_all=('creature',), subtypes_all=(subtype,))
    return fixed_spell_predicate(subject + ' spells you cast')


def turn_cast_grant_handler(text):
    material = ' '.join(text.strip().split())
    if _GRANTED_ESCAPE.fullmatch(material):
        return "source-granted-escape-v1", granted_escape_descriptor(), (GRANTED_ESCAPE_CAPABILITY,)
    material=re.sub(r"^[A-Z][A-Za-z' ]{0,80} — (?=Once during each of your turns,)",'',material)
    if re.fullmatch(
        r"During each of your turns, you may play a land and cast a permanent spell of each "
        r"permanent type from your graveyard\.(?: \(If a card has multiple permanent types, choose one "
        r"as you play it\.\))?", material, re.I,
    ):
        spec = permanent_type_graveyard_grant()
        return ("permanent-types-own-turn-graveyard-grant-v1", {
            "handler_id": spec.handler_id, "schema_version": 1, "event": TURN_CAST_GRANT_EVENT,
            "requires_complete_card_program": True, "grant": spec.to_dict(),
        }, spec.capability_dependencies)
    qualified = _qualified_graveyard_grant(material)
    if qualified is not None:
        return ('once-own-turn-qualified-grant-v1',{'handler_id':qualified.handler_id,'schema_version':1,
            'event':TURN_CAST_GRANT_EVENT,'requires_complete_card_program':True,'grant':qualified.to_dict()},qualified.capability_dependencies)
    rider = 'none'
    for suffix, kind in (
            (' If a spell cast this way would be put into your graveyard, exile it instead.', 'graveyard_exile'),
            (' If you cast a spell this way, that artifact enters tapped.', 'tapped_entry')):
        if material.casefold().endswith(suffix.casefold()):
            material = material[:-len(suffix)]
            rider = kind
            break
    match = _CLAUSE.fullmatch(material)
    if match is None:
        return None
    subject = re.sub(r'^(?:a|an) ', '', match['subject'], flags=re.I).casefold()
    if subject == 'a':
        predicates = (ObjectQuerySpec(),)
    elif subject == 'noncreature or robot':
        predicates = (ObjectQuerySpec(excluded_types=('creature',)), ObjectQuerySpec(subtypes_all=('Robot',)))
    else:
        predicates = tuple(_spell_predicate(part) for part in subject.split(' or '))
        if any(predicate is None for predicate in predicates):
            return None
    origins = {'your hand': ('hand',), 'your graveyard': ('graveyard',),
        'your hand or the top of your library': ('hand', 'library_top')}[match['origin'].casefold()]
    try:
        spec = TurnCastGrantSpec(origins, predicates, match['free'] is not None, rider)
    except (TypeError, ValueError):
        return None
    return ('once-own-turn-cast-grant-v1', {'handler_id': TURN_CAST_RIDER_HANDLERS[spec.cast_rider],
        'schema_version': 1, 'event': TURN_CAST_GRANT_EVENT, 'requires_complete_card_program': True, 'grant': spec.to_dict()},
        spec.capability_dependencies)


def _qualified_graveyard_grant(text):
    if re.fullmatch(r'Once during each of your turns, you may cast a creature spell from among cards in your graveyard that were put there from anywhere other than the battlefield this turn\.?',text,re.I):
        return TurnCastGrantSpec(('graveyard',),(ObjectQuerySpec(types_all=('creature',)),),qualification='nonbattlefield_arrival_this_turn')
    if re.fullmatch(r'Once during each of your turns, you may cast a spell from among cards in your graveyard that were milled this turn\.?',text,re.I):
        return TurnCastGrantSpec(('graveyard',),(ObjectQuerySpec(),),qualification='milled_this_turn')
    if re.fullmatch(r'Once during each of your turns, you may play a land or cast a permanent spell from among cards in your graveyard that were put there from your library this turn\.?',text,re.I):
        return TurnCastGrantSpec(('graveyard',),(ObjectQuerySpec(types_any=('artifact','battle','creature','enchantment','planeswalker')),),
            qualification='library_arrival_this_turn',allow_land_play=True)
    return None
