from __future__ import annotations

"""Defender-only static grants through existing layer-six query owners."""

import re
from ..ability_fragments import ability_fragment_to_dict
from ..defender_permission import (
    DefenderAttackPermission, DEFENDER_PERMISSION_CAPABILITY,
    DEFENDER_PERMISSION_HANDLER, CONDITIONAL_DEFENDER_PERMISSION_HANDLER,
    DEFENDER_TEMPORARY_MECHANIC, DEFENDER_TEMPORARY_OPERATION,
)
from ..declaration_costs import normalized_oracle_line
from .continuous_templates import (
    fixed_query_keyword_grant_handler, attached_fixed_characteristics_handler,
    fixed_public_state_characteristics_handler,
)
from .fixed_resolution_characteristics import _subject
from .fixed_target_effect_sequences import _keyword_list

_PERMISSION = re.compile(r"can attack as though (?:it|they) didn't have defender", re.IGNORECASE)


def static_defender_permission_handler(text: str, *, source_name: str):
    normalized = normalized_oracle_line(text, card_name=source_name)
    fragment = ability_fragment_to_dict(DefenderAttackPermission())
    if normalized == "this creature can attack as though it didn't have defender.":
        return ('static-defender-attack-permission-v1', {
            'handler_id': DEFENDER_PERMISSION_HANDLER, 'schema_version': 1,
            'event': 'continuous', 'fragment': fragment,
        }, (DEFENDER_PERMISSION_CAPABILITY,))
    if 'until end' in text.casefold() or len(_PERMISSION.findall(text)) != 1:
        return None
    # Existing fixed keyword grammar determines subject, condition and any
    # fixed P/T prefix. Replace only its sentinel grant with the typed fact.
    synthetic = _PERMISSION.sub('has vigilance', text)
    synthetic = re.sub(r', it has vigilance', ', this creature has vigilance', synthetic, flags=re.I)
    synthetic = re.sub(r'and has ([^.]+?) and has vigilance', r'and has \1 and vigilance', synthetic, flags=re.I)
    synthetic = re.sub(r'has ([^.]+?) and has vigilance', r'has \1 and vigilance', synthetic, flags=re.I)
    compiled = fixed_public_state_characteristics_handler(synthetic, source_name=source_name)
    conditional = compiled is not None
    if compiled is None:
        compiled = attached_fixed_characteristics_handler(synthetic, source_name=source_name)
    if compiled is None:
        compiled = fixed_query_keyword_grant_handler(synthetic)
    if compiled is None:
        return None
    _, raw, required = compiled
    handler = dict(raw); modifier = dict(handler['modifier'])
    abilities = list(modifier.get('add_abilities', ()))
    if abilities.count('Vigilance') != 1:
        return None
    abilities.remove('Vigilance');modifier['add_abilities'] = abilities
    modifier['add_ability_fragments'] = [fragment]
    handler['modifier'] = modifier
    capabilities = set(required) - {'combat.attack.vigilance'}
    capabilities.add(DEFENDER_PERMISSION_CAPABILITY)
    if conditional:
        handler.update(handler_id=CONDITIONAL_DEFENDER_PERMISSION_HANDLER, schema_version=3)
        if not abilities:capabilities.discard('continuous.ability.fixed_query_keyword_grant')
    elif handler['handler_id'] == 'continuous.ability.fixed-query-keyword-grant.v1':
        handler['handler_id'] = 'continuous.ability.fixed-query-grant.v1'
        capabilities.discard('continuous.ability.fixed_query_keyword_grant')
        capabilities.add('continuous.ability.fixed_query_grant')
    return 'static-defender-permission-composition-v1', handler, tuple(sorted(capabilities))


def temporary_defender_permission_template(text: str, *, source_name: str,
        source_is_permanent: bool | None, source_card_types: tuple[str, ...]):
    match = re.fullmatch(
        r"(?P<subject>.+?) (?:(?:gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) "
        r"until end of turn(?: and gains (?P<pt_keywords>.+?) until end of turn)? and )|"
        r"(?:gains? (?P<keywords>.+?) until end of turn and ))?"
        r"can attack this turn as though (?:it|they) didn't have defender\.?",
        text.strip(), re.IGNORECASE,
    )
    if match is None:
        return None
    selected = _subject(match['subject'], source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if selected is None:
        return None
    selection, schema = selected
    if schema is not None and schema.get('types_any',schema.get('types_all'))!=['creature']:return None
    keywords = _keyword_list(match['keywords'] or match['pt_keywords']) if (match['keywords'] or match['pt_keywords']) else ()
    if keywords is None:
        return None
    instruction = {'op': DEFENDER_TEMPORARY_OPERATION, 'schema_version': 3, 'permission': 'ignore_defender',
        'power': int(match['power'] or 0), 'toughness': int(match['toughness'] or 0),
        'keywords': list(keywords), **selection}
    return ('fixed-temporary-defender-permission-v1', (instruction,), schema,
        (DEFENDER_TEMPORARY_MECHANIC, 'cr-611-continuous-effects',
            *(('cr-115-targets',) if schema is not None else ()),
            *(keyword.casefold() for keyword in keywords)))
