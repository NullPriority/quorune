"""Read sealed declaration snapshots; commit no game state or trigger batches."""

from collections.abc import Mapping
from dataclasses import replace

from ..attack_batch_model import (ATTACK_BATCH_CONTEXT,QualifiedAttackBatchSpec,
    QUALIFIED_ATTACK_BATCH_CAPABILITY,attack_batch_conditions)
from ..qualified_attack_model import ATTACK_FACT_PREFIX,ATTACK_FACT_COLLECTIONS,ATTACK_FACT_BOOLEANS,ATTACK_FACT_NUMBERS
from ..object_query import ObjectQueryResult,object_matches_query
from ..replacement.immutable import FrozenMap


def seal_attack_declaration(event,characteristic_contexts):
    participants={p.object_id:p for p in event.participants}
    return tuple(FrozenMap({'reference':participants[a.attacker_object_id].reference,
        'logical_object_id':participants[a.attacker_object_id].logical_object_id,
        'controller':participants[a.attacker_object_id].controller,
        'recipient_kind':a.recipient.kind.value,'recipient':a.recipient.reference,
        'recipient_logical_object_id':a.recipient.logical_object_id,
        'defending_player':a.recipient.defending_player,
        'characteristics':dict(characteristic_contexts[a.attacker_object_id])}) for a in event.assignments)


def _sealed_rows(context):
    rows=context.get(ATTACK_BATCH_CONTEXT)
    if not isinstance(rows,(tuple,list)) or not rows:raise ValueError('Qualified attack batch requires sealed declaration facts')
    seen=set();identities=set();controllers=set()
    for row in rows:
        if not isinstance(row,Mapping) or set(row)!={'reference','logical_object_id','controller','recipient_kind','recipient','recipient_logical_object_id','defending_player','characteristics'}:
            raise ValueError('Malformed sealed attacker')
        if any(type(row[k]) is not str or not row[k] for k in ('reference','logical_object_id','controller','recipient','defending_player')):
            raise ValueError('Sealed attacker identities must be nonempty strings')
        if row['reference'] in seen:raise ValueError('A declared attacker occurs once')
        seen.add(row['reference'])
        if row['logical_object_id'] in identities:raise ValueError('An attacker incarnation occurs once')
        identities.add(row['logical_object_id']);controllers.add(row['controller'])
        if row['recipient_kind'] not in {'player','planeswalker','battle'}:raise ValueError('Unknown attack recipient kind')
        if row['recipient_kind']=='player' and row['recipient']!=row['defending_player']:
            raise ValueError('Attacked player and defending player differ')
        identity=row['recipient_logical_object_id']
        if row['recipient_kind']=='player' and identity is not None or row['recipient_kind']!='player' and (type(identity) is not str or not identity):
            raise ValueError('Attack recipient incarnation is inconsistent with its kind')
        facts=row['characteristics']
        expected={'version',*ATTACK_FACT_COLLECTIONS,*ATTACK_FACT_BOOLEANS,*ATTACK_FACT_NUMBERS}
        if not isinstance(facts,Mapping) or set(facts)!={ATTACK_FACT_PREFIX+k for k in expected}:
            raise ValueError('Sealed attacker characteristic fields changed')
        if type(facts[ATTACK_FACT_PREFIX+'version']) is not int or facts[ATTACK_FACT_PREFIX+'version']!=1:raise ValueError('Unknown sealed attacker version')
        for name in ATTACK_FACT_COLLECTIONS:
            values=facts[ATTACK_FACT_PREFIX+name]
            if not isinstance(values,(tuple,list)) or any(type(v) is not str or not v for v in values) or len(values)!=len(set(values)):
                raise ValueError('Sealed attacker collections must be distinct strings')
        if any(type(facts[ATTACK_FACT_PREFIX+name]) is not bool for name in ATTACK_FACT_BOOLEANS):raise ValueError('Sealed attacker state must be boolean')
        if 'creature' not in facts[ATTACK_FACT_PREFIX+'types'] or not facts[ATTACK_FACT_PREFIX+'attacking']:
            raise ValueError('Sealed declaration members must be attacking creatures')
        for name in ATTACK_FACT_NUMBERS:
            value=facts[ATTACK_FACT_PREFIX+name]
            if value is not None and type(value) is not int or name=='total_counters' and (type(value) is not int or value<0):
                raise ValueError('Sealed attacker numeric facts changed')
        if len(facts[ATTACK_FACT_PREFIX+'counter_names'])>facts[ATTACK_FACT_PREFIX+'total_counters']:
            raise ValueError('Sealed counter kinds exceed the public total')
    if len(controllers)!=1:raise ValueError('One declaration has one attacking controller')
    return rows


def _row_matches(source,spec,row):
    if row['controller']!=source.controller or spec.exclude_source and row['reference']==source.ref:return False
    f={k.removeprefix(ATTACK_FACT_PREFIX):v for k,v in row['characteristics'].items()}
    state={name:f[name] for name in ATTACK_FACT_BOOLEANS-{'monstrous'}}
    candidate=ObjectQueryResult(row['logical_object_id'],row['reference'],'Declared attacker',row['controller'],row['controller'],'battlefield',
        types=tuple(f['types']),subtypes=tuple(f['subtypes']),supertypes=tuple(f['supertypes']),colors=tuple(f['colors']),keywords=tuple(f['keywords']),
        counters={name:1 for name in f['counter_names']},effective_power=f['power'],effective_toughness=f['toughness'],
        monstrous_value=1 if f['monstrous'] else None,logical_object_id=row['logical_object_id'],**state)
    query=spec.query
    if query.state_predicate is not None and query.state_predicate.minimum_total_counter_count is not None:
        if f['total_counters']<query.state_predicate.minimum_total_counter_count:return False
        state=query.state_predicate
        remaining=(state.entered_this_turn or state.counter_name is not None or any(getattr(state,name) is not None
            for name in ('tapped','attacking','blocking','enchanted','equipped','modified','monstrous')))
        query=replace(query,state_predicate=replace(state,minimum_total_counter_count=None) if remaining else None)
    return object_matches_query(candidate,query)


def qualified_attack_match_count(source,condition,context):
    spec=QualifiedAttackBatchSpec.from_dict(condition['attack_batch']);rows=_sealed_rows(context)
    allowed={'declaration':{'player','planeswalker','battle'},'player':{'player'},'player_or_planeswalker':{'player','planeswalker'}}[spec.grouping]
    total=0
    occurrence=next((row for row in rows if row['reference']==context.get('card')),None)
    if occurrence is None:raise ValueError('Attack occurrence is absent from its sealed declaration')
    if context.get('controller',occurrence['controller'])!=occurrence['controller']:
        raise ValueError('Attack occurrence controller differs from its sealed declaration')
    if occurrence['recipient_kind'] not in allowed:return 0
    if spec.grouping!='declaration' and (occurrence['recipient_kind'],occurrence['recipient'])!=(context.get('attacked_recipient_kind'),context.get('defender')):
        raise ValueError('Attack occurrence recipient differs from its sealed declaration')
    if not _row_matches(source,spec,occurrence):return 0
    for row in rows:
        if row['controller']!=source.controller or row['recipient_kind'] not in allowed:continue
        if spec.grouping!='declaration' and (row['recipient_kind'],row['recipient'])!=(context.get('attacked_recipient_kind'),context.get('defender')):continue
        if _row_matches(source,spec,row):total+=1
    return total


def attack_batch_aggregation_scope(program,context):
    specs=attack_batch_conditions(program.event_condition)
    if not specs or specs[0].grouping=='declaration':return {}
    _sealed_rows(context)
    kind=context.get('attacked_recipient_kind');reference=context.get('defender')
    if kind not in {'player','planeswalker'} or type(reference) is not str or not reference:raise ValueError('Grouped attack lost its recipient')
    row=next((row for row in context[ATTACK_BATCH_CONTEXT] if row['reference']==context.get('card')),None)
    if row is None:raise ValueError('Grouped attack occurrence is absent')
    return {'attacked_recipient_kind':kind,'attacked_recipient':reference,'attacked_recipient_logical_object_id':row['recipient_logical_object_id']}


def qualified_attack_batch_runtime_dependencies(program):
    specs=attack_batch_conditions(program.event_condition)
    if specs and (program.event!='creature.attacks' or 'one_or_more_event_batch' not in program.coverage):
        raise ValueError('Qualified attack batches require declared-attack coalescing')
    return (QUALIFIED_ATTACK_BATCH_CAPABILITY,) if specs else ()
