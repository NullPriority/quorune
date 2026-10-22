from __future__ import annotations

"""Closed live player and attached-recipient quantities for static modifiers."""

import re
from ..characteristic_fragments import (CharacteristicQuantitySpec,CharacteristicQuantityScope as Scope,
    CharacteristicQuantityReduction as Reduction)
from ..object_predicate import ObjectQuerySpec


def source_modifier_quantity(text):
    text=' '.join(text.strip().rstrip('.').split())
    if text.casefold()=='your life total':
        return CharacteristicQuantitySpec(Scope.CONTROLLER_LIFE_TOTAL,schema_version=4)
    if re.fullmatch(r'poison counters? your opponents have',text,re.I):
        return CharacteristicQuantitySpec(Scope.OPPONENT_POISON_COUNTERS,schema_version=4)
    if re.fullmatch(r'Auras? and Equipment attached to (?:it|this creature)',text,re.I):
        return CharacteristicQuantitySpec(Scope.ATTACHED_TO_SOURCE,
            query=ObjectQuerySpec(zones=('battlefield',),types_any=('artifact','enchantment'),
                subtypes_any=('aura','equipment')),schema_version=2)
    return None


def attached_object_quantity(text):
    text=' '.join(text.strip().rstrip('.').split()).casefold()
    reduction={'of its colors':Reduction.DISTINCT_COLORS,'its mana value':Reduction.MAXIMUM_MANA_VALUE}.get(text)
    return CharacteristicQuantitySpec(Scope.ATTACHED_OBJECT,schema_version=4,reduction=reduction) if reduction is not None else None


def quantified_public_modifier_handler(text):
    from .public_state_queries import fixed_characteristic_battlefield_query_subject
    from .query_characteristic_templates import query_characteristic_quantity,query_characteristic_value
    from .continuous_templates import fixed_query_keyword_grant_handler
    from .creature_subtypes import canonical_creature_subtype_surface
    from ..semantic_runtime.quantified_anthems import (QuantifiedAnthemHandler,
        QUANTIFIED_ANTHEM_HANDLER,QUANTIFIED_ANTHEM_CAPABILITY)
    fixed=re.fullmatch(r'(?P<subject>.+?) gets? (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) for each '
        r'(?P<quantity>.+?)(?: and ha(?:ve|s) (?P<abilities>.+?))?\.',text.strip(),re.I)
    variable=re.fullmatch(r'(?P<subject>.+?) gets? (?P<power>[+-](?:X|0))/(?P<toughness>[+-](?:X|0)), '
        r'where X is (?P<quantity>.+?)\.',text.strip(),re.I)
    match=fixed or variable
    if match is None:return None
    if match['subject'].casefold() in {'this creature','equipped creature','enchanted creature','enchanted permanent','fortified land'}:
        return None
    subject=fixed_characteristic_battlefield_query_subject(match['subject'])
    if subject is None:return None
    relation,predicate,exclude_source=subject
    phrase=match['quantity'];exclude_recipient=False
    other=re.fullmatch(r'other (?P<subtype>[A-Za-z]+) on the battlefield',phrase,re.I)
    if other is not None:
        subtype=canonical_creature_subtype_surface(other['subtype'])
        if subtype is None:return None
        quantity=CharacteristicQuantitySpec(Scope.ALL_ZONES,
            query=ObjectQuerySpec(zones=('battlefield',),subtypes_all=(subtype,)),schema_version=2)
        exclude_recipient=True
    elif variable is not None:
        quantity=query_characteristic_value(phrase,source_name='source')
    else:
        quantity=query_characteristic_quantity(phrase,source_name='source',definition_extensions=True)
    if quantity is None:return None
    coefficient=lambda value:0 if value.endswith('0') else -1 if value.startswith('-') else 1
    power=coefficient(match['power']) if variable is not None else int(match['power'])
    toughness=coefficient(match['toughness']) if variable is not None else int(match['toughness'])
    descriptor={'handler_id':QUANTIFIED_ANTHEM_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'condition':{'target_controller':relation,'predicate':predicate.to_dict(),'exclude_source':exclude_source},
        'modifier':{'power':power,'toughness':toughness,'quantity':quantity.to_dict(),'exclude_recipient':exclude_recipient}}
    try:QuantifiedAnthemHandler().validate(descriptor)
    except (TypeError,ValueError):return None
    descriptors=[descriptor]
    capabilities={'continuous.power_toughness.fixed_anthem',QUANTIFIED_ANTHEM_CAPABILITY}
    abilities=match.groupdict().get('abilities')
    if abilities:
        keyword=fixed_query_keyword_grant_handler(f"{match['subject']} have {abilities}.")
        if keyword is None:return None
        descriptors.append(keyword[1]);capabilities.update(keyword[2])
    return 'continuous-public-query-anthem-v1',tuple(descriptors),tuple(sorted(capabilities))
