"""Closed source/other-creature heads over the existing block relationship owner."""
import re
from ..object_predicate import ObjectQuerySpec
from ..block_characteristic_model import BLOCK_CHARACTERISTIC_CONDITION,BLOCK_CHARACTERISTIC_CAPABILITY,block_counterpart_query
from .direct_target import direct_permanent_target_spec


def qualified_block_pair_spec(text):
    match=re.fullmatch(r'(?:When|Whenever) this creature (?P<event>blocks or becomes blocked by|becomes blocked by|blocks) (?P<other>an? .+?), (?P<body>.+)',text,re.I)
    if match is None:return None
    subject=re.sub(r'^an? ','',match['other'],flags=re.I)
    spec=direct_permanent_target_spec('target '+subject)
    if spec is None:return None
    creature_subject='creature' in (*spec.types_all,*spec.types_any) or bool(spec.subtypes_any)
    if not creature_subject:return None
    if spec.types_any not in ((),('creature',)):return None
    query=ObjectQuerySpec(zones=('battlefield',),types_all=tuple(sorted(set(spec.types_all)|{'creature'})),
        types_any=() if spec.types_any==('creature',) else spec.types_any,excluded_types=spec.types_none,
        subtypes_any=spec.subtypes_any,excluded_subtypes=spec.subtypes_none,supertypes_all=spec.supertypes_any,
        excluded_supertypes=spec.supertypes_none,colors_any=spec.colors_any,colors_none=spec.colors_none,
        colorless=spec.colorless,keywords_all=spec.keywords_all,keywords_none=spec.keywords_none,numeric_characteristic=spec.numeric_characteristic)
    try:block_counterpart_query(query.to_dict())
    except (ValueError,TypeError):return None
    if spec!=type(spec)(types_any=spec.types_any,types_all=spec.types_all,types_none=spec.types_none,subtypes_any=spec.subtypes_any,
            subtypes_none=spec.subtypes_none,supertypes_any=spec.supertypes_any,supertypes_none=spec.supertypes_none,
            colors_any=spec.colors_any,colors_none=spec.colors_none,colorless=spec.colorless,keywords_all=spec.keywords_all,
            keywords_none=spec.keywords_none,numeric_characteristic=spec.numeric_characteristic):return None
    from .fixed_public_event_trigger_bindings import _spec
    from .fixed_public_multi_event_bindings import _source_condition
    from ..rules.event_subscriptions import FixedEventSubscription,fixed_event_subscription_condition
    event=match['event'].casefold();qualifier={'field':BLOCK_CHARACTERISTIC_CONDITION,'op':'eq','value':True,'query':query.to_dict()}
    conditions={'creature.blocks':_source_condition({'field':'blocked_attacker','op':'truthy','value':True},qualifier),
        'creature.becomes_blocked_by':_source_condition(qualifier)}
    events=('creature.blocks','creature.becomes_blocked_by') if event=='blocks or becomes blocked by' else ('creature.blocks',) if event=='blocks' else ('creature.becomes_blocked_by',)
    condition=fixed_event_subscription_condition(tuple(FixedEventSubscription(name,conditions[name]) for name in events)) if len(events)>1 else conditions[events[0]]
    return _spec(events[0],'source_block_pair_counterpart',match['body'],'fixed-source-block-pair-trigger-v1',
        'trigger-event-normalized-public-action',condition=condition,
        capabilities=(BLOCK_CHARACTERISTIC_CAPABILITY,'trigger.event.block_pair_counterpart',*(('trigger.subscription.fixed_multi_event',) if len(events)>1 else ())))
