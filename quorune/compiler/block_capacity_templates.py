from __future__ import annotations

"""Fixed blocking capacity composed through existing source/query/attachment grants."""

import re
from ..ability_fragments import ability_fragment_to_dict
from ..block_capacity_model import BlockCapacitySpec, BLOCK_CAPACITY_CAPABILITY, BLOCK_CAPACITY_HANDLER
from ..declaration_costs import normalized_oracle_line
from .continuous_templates import (
    fixed_query_keyword_grant_handler, attached_fixed_characteristics_handler,
    fixed_public_state_characteristics_handler,
)

_PERMISSION = re.compile(r'can block (?:(?:an additional (?P<count>creature|one creature|two creatures|three creatures|seven creatures|ninety-nine creatures) each combat)|(?P<any>any number of creatures))', re.I)
_COUNTS = {'creature':1,'one creature':1,'two creatures':2,'three creatures':3,'seven creatures':7,'ninety-nine creatures':99}


def static_block_capacity_handler(text, *, source_name):
    matches=list(_PERMISSION.finditer(text))
    if len(matches)!=1 or 'until end' in text.casefold():return None
    match=matches[0]
    spec=BlockCapacitySpec(unlimited=True) if match['any'] else BlockCapacitySpec(additional=_COUNTS[match['count'].casefold()])
    fragment=ability_fragment_to_dict(spec)
    normalized=normalized_oracle_line(text,card_name=source_name)
    if _PERMISSION.fullmatch(normalized.removeprefix('this creature ').rstrip('.')):
        return ('fixed-source-block-capacity-v1',{'handler_id':BLOCK_CAPACITY_HANDLER,'schema_version':1,'event':'characteristics.evaluate','fragment':fragment},(BLOCK_CAPACITY_CAPABILITY,))
    printed_vigilance=bool(re.search(r'\bhas vigilance\b',text,re.I))
    synthetic=_PERMISSION.sub('has vigilance',text)
    synthetic=re.sub(r', has vigilance, and has vigilance',', and has vigilance',synthetic,flags=re.I)
    synthetic=re.sub(r'(?P<stats>gets [+-]\d+/[+-]\d+), and has vigilance',r'\g<stats> and has vigilance',synthetic,flags=re.I)
    synthetic=re.sub(r', it has vigilance',', this creature has vigilance',synthetic,flags=re.I)
    synthetic=re.sub(r'and has ([^.]+?) and has vigilance',r'and has \1 and vigilance',synthetic,flags=re.I)
    synthetic=re.sub(r'has ([^.]+?) and has vigilance',r'has \1 and vigilance',synthetic,flags=re.I)
    compiled=fixed_public_state_characteristics_handler(synthetic,source_name=source_name)
    conditional=compiled is not None
    if compiled is None:compiled=attached_fixed_characteristics_handler(synthetic,source_name=source_name)
    if compiled is None:compiled=fixed_query_keyword_grant_handler(synthetic)
    if compiled is None:return None
    _,raw,required=compiled;handler=dict(raw);modifier=dict(handler['modifier']);abilities=list(modifier.get('add_abilities',()))
    # An existing vigilance in the same sentence remains a separate printed grant.
    if 'Vigilance' not in abilities:return None
    if not printed_vigilance:abilities.remove('Vigilance')
    modifier['add_abilities']=abilities;modifier['add_ability_fragments']=[*modifier.get('add_ability_fragments',()),fragment];handler['modifier']=modifier
    capabilities=set(required);capabilities.add(BLOCK_CAPACITY_CAPABILITY)
    if not abilities:capabilities.discard('combat.attack.vigilance')
    if conditional:handler.update(handler_id='continuous.block-capacity.public-state.v1',schema_version=5)
    if handler['handler_id']=='continuous.ability.fixed-query-keyword-grant.v1':
        handler['handler_id']='continuous.ability.fixed-query-grant.v1';capabilities.discard('continuous.ability.fixed_query_keyword_grant');capabilities.add('continuous.ability.fixed_query_grant')
    return 'fixed-composed-block-capacity-v1',handler,tuple(sorted(capabilities))


def temporary_block_capacity_template(text, *, source_name, source_is_permanent, source_card_types):
    from .fixed_resolution_characteristics import _subject
    from ..block_capacity_rule_model import TEMPORARY_BLOCK_CAPACITY_MECHANIC
    match = re.fullmatch(
        r'(?P<subject>.+?) (?:(?:gets (?P<p>[+-]\d+)/(?P<t>[+-]\d+) until end of turn and )?)'
        r'can block (?:(?:an additional (?P<count>creature|one creature|two creatures|three creatures|seven creatures|ninety-nine creatures))|'
        r'(?P<any>any number of creatures)) this turn\.?', text.strip(), re.I)
    if match is None:
        return None
    selected = _subject(match['subject'], source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=tuple(source_card_types))
    if selected is None:
        return None
    selection, schema = selected
    if 'predicate' in selection:
        return None
    if schema is None:
        if selection != {'card': '$source.zone_object'} or 'creature' not in source_card_types:
            return None
    elif schema.get('types_any', schema.get('types_all')) != ['creature']:
        return None
    capacity = BlockCapacitySpec(unlimited=True) if match['any'] else BlockCapacitySpec(additional=_COUNTS[match['count'].casefold()])
    effect = {'op': 'apply_source_characteristics_until_end_of_turn', 'schema_version': 10,
        **selection, 'capacity': capacity.to_dict(), 'power': int(match['p'] or 0), 'toughness': int(match['t'] or 0)}
    return ('fixed-temporary-block-capacity-v1', (effect,), schema,
        (TEMPORARY_BLOCK_CAPACITY_MECHANIC, 'cr-611-continuous-effects',
            *(('cr-115-targets',) if schema is not None else ())))
