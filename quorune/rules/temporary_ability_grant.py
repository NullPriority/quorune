from __future__ import annotations

"""Closed temporary ability bundles over a revalidated creature incarnation."""

from typing import Mapping
from ..ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES

BACKUP_CAPABILITY='trigger.keyword.backup'
BACKUP_COVERAGE='printed_backup_ability_bundle'
BACKUP_CONTEXT='backup_source_printed'
OPERATION='apply_source_characteristics_until_end_of_turn'
TEMPORARY_QUOTED_GRANT_CAPABILITY = 'continuous.ability.temporary_quoted_grant'
TEMPORARY_QUOTED_GRANT_MECHANIC = 'temporary-quoted-ability-grant'


def temporary_quoted_grant_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities
    if TEMPORARY_QUOTED_GRANT_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    grant = effects[0]
    if grant.get('schema_version') == 9:
        if (set(grant) != {'op', 'schema_version', 'card', 'power', 'toughness', 'ability_fragments'}
                or type(grant['power']) is not int or type(grant['toughness']) is not int):
            return ()
        grant = {'op': grant['op'], 'schema_version': 7, 'card': grant['card'], 'keywords': [],
            'ability_fragments': grant['ability_fragments'], 'exclude_source': False, 'printed_only': False}
    if grant.get('schema_version') == 8:
        from ..object_predicate import ObjectQuerySpec
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        if set(grant) != {'op', 'schema_version', 'predicate', 'ability_fragments'} or target_schema is not None:
            return ()
        try:
            query = ObjectQuerySpec.from_dict(grant['predicate'])
        except (TypeError, ValueError):
            return ()
        if (query.to_dict() != grant['predicate'] or query.keywords_none or query.colors_none
                or not fixed_resolution_characteristic_query_is_closed(query, target_schema=None)):
            return ()
        return _temporary_query_grant_capabilities(grant, query)
    if not direct_permanent_target_schema_is_closed(target_schema) or target_schema.get('types_any') != ['creature']:
        return ()
    try:
        keywords, fragments = decode_temporary_ability_grant(grant)
    except (TypeError, ValueError, KeyError):
        return ()
    if (grant['card'] != '$target.0' or grant['printed_only'] or grant['exclude_source']
            or keywords or len(fragments) != 1 or fragments[0]['kind'] not in {'granted_activated', 'granted_triggered'}):
        return ()
    return (TEMPORARY_QUOTED_GRANT_CAPABILITY, 'target.revalidate_resolution',
        *direct_target_predicate_capabilities(target_schema))


def _temporary_query_grant_capabilities(grant, query):
    try:
        _, fragments = decode_temporary_ability_grant({
            'op': grant['op'], 'schema_version': 7, 'card': '$target.0', 'keywords': [],
            'ability_fragments': grant['ability_fragments'], 'exclude_source': False, 'printed_only': False})
    except (TypeError, ValueError, KeyError):
        return ()
    if len(fragments) != 1 or fragments[0]['kind'] not in {'granted_activated', 'granted_triggered'}:
        return ()
    return (TEMPORARY_QUOTED_GRANT_CAPABILITY,
        *(['state_query.permanent.public_state_predicate'] if query.state_predicate is not None else []))


def decode_temporary_ability_grant(effect):
    fields={'op','schema_version','card','keywords','ability_fragments','exclude_source','printed_only'}
    if not isinstance(effect,Mapping) or set(effect)!=fields:raise ValueError('Temporary ability bundle fields are malformed')
    if effect['op']!=OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=7:raise ValueError('Temporary ability bundle version is unsupported')
    if type(effect['card']) is not str or not effect['card']:raise ValueError('Temporary ability bundle requires one creature reference')
    if type(effect['exclude_source']) is not bool or type(effect['printed_only']) is not bool:raise ValueError('Temporary ability bundle conditions must be Boolean')
    keywords=effect['keywords'];raw=effect['ability_fragments']
    if not isinstance(keywords,(list,tuple)) or any(type(k) is not str or k not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES for k in keywords) or len(set(keywords))!=len(keywords):raise ValueError('Temporary ability bundle keywords are unsupported')
    if not isinstance(raw,(list,tuple)) or len(raw)>12:raise ValueError('Temporary ability bundle fragments are malformed')
    allowed={'granted_activated','granted_triggered','declaration_restriction','declaration_requirement','protection','ward','toxic','toughness_assignment_rule','defender_attack_permission','as_unblocked_assignment'}
    fragments=[]
    for value in raw:
        if not isinstance(value,Mapping) or value.get('kind') not in allowed:raise ValueError('Temporary ability bundle fragment kind is unsupported')
        fragments.append(ability_fragment_to_dict(ability_fragment_from_dict(value)))
    # Separate granted triggered abilities retain rules-significant multiplicity.
    return tuple(keywords),tuple(fragments)


def backup_node_capabilities(*,effects,target_schema,mechanic_ids,cost_schema=None):
    if 'backup' not in mechanic_ids or cost_schema is not None or len(effects)!=2:return ()
    counter,grant=effects
    if not isinstance(counter,Mapping) or not isinstance(grant,Mapping) or not isinstance(target_schema,Mapping):return ()
    if dict(target_schema or {})!={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1}:return ()
    if dict(counter)!={'op':'place_counters','card':'$target.0','counter':'+1/+1','amount':counter.get('amount'),'source':'$source'} or type(counter.get('amount')) is not int or not 1<=counter['amount']<=10:return ()
    try:keywords,fragments=decode_temporary_ability_grant(grant)
    except (TypeError,ValueError,KeyError):return ()
    if grant['card']!='$target.0' or grant['exclude_source'] is not True or grant['printed_only'] is not True:return ()
    return tuple(sorted({BACKUP_CAPABILITY,'counter.producer.fixed_effect','target.revalidate_resolution',*(cap for k in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[k])}))
