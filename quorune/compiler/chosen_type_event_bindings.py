"""Closed chosen-creature-type casts and public creature occurrences."""
import re
from ..rules.chosen_type_events import CHOSEN_TYPE_EVENT_CAPABILITY,CHOSEN_TYPE_FIELD
from ..rules.event_subscriptions import FixedEventSubscription,fixed_event_subscription_condition
from ..qualified_attack_model import ATTACK_FACT_PREFIX,QUALIFIED_ATTACK_CAPABILITY

CHOSEN_TYPE_EVENT_VARIANTS=frozenset({'chosen_type_cast','chosen_type_entry','chosen_type_death','chosen_type_entry_attack'})


def _conditions(*,creature,controlled,other=False,nontoken=False,prefix=''):
    result=[{'field':CHOSEN_TYPE_FIELD,'op':'in','value':'$context.'+prefix+'subtypes'}]
    if creature:result.append({'field':prefix+'types','op':'contains_any','value':['creature']})
    if controlled:result.append({'field':'controller','op':'eq','value':'$source.controller'})
    if other:result.append({'field':'card','op':'ne','value':'$source.ref'})
    if nontoken:result.append({'field':prefix+'token','op':'eq','value':False})
    if prefix:result.append({'field':prefix+'version','op':'eq','value':1})
    return result[0] if len(result)==1 else {'all':result}


def chosen_type_event_binding_spec(text,*,card_name=None):
    del card_name
    from .fixed_public_event_trigger_bindings import _spec
    cast=re.fullmatch(r'Whenever (?P<actor>you cast|a player casts) a (?P<creature>creature )?spell of the chosen (?:creature )?type, (?P<body>.+)',text,re.I)
    if cast:
        return _spec('spell.cast','chosen_type_cast',cast['body'],'fixed-counter-chosen-type-cast-trigger-v1',
            'trigger-event-normalized-spell-cast',condition=_conditions(creature=bool(cast['creature']),
                controlled=cast['actor'].casefold()=='you cast'),capabilities=(CHOSEN_TYPE_EVENT_CAPABILITY,))
    match=re.fullmatch(r'(?:When|Whenever) (?P<article>a|an|another) (?P<nontoken>nontoken )?'
        r'(?P<kind>creature|permanent)(?P<before> you control)? of the chosen (?:creature )?type'
        r'(?P<after> you control)? (?P<event>enters or attacks|enters|dies), (?P<body>.+)',text,re.I)
    if match is None or match['before'] and match['after']:return None
    creature=match['kind'].casefold()=='creature'
    if not creature and match['event'].casefold()!='enters':return None
    fields={'creature':creature,'controlled':bool(match['before'] or match['after']),
        'other':match['article'].casefold()=='another','nontoken':bool(match['nontoken'])}
    condition=_conditions(**fields)
    event=match['event'].casefold()
    caps=(CHOSEN_TYPE_EVENT_CAPABILITY,)
    if event=='enters or attacks':
        condition=fixed_event_subscription_condition((FixedEventSubscription('permanent.enter',condition),
            FixedEventSubscription('creature.attacks',_conditions(**fields,prefix=ATTACK_FACT_PREFIX))))
        caps=(*caps,'trigger.subscription.fixed_multi_event',QUALIFIED_ATTACK_CAPABILITY,'trigger.event.normalized_public_action')
    return _spec('creature.dies' if event=='dies' else 'permanent.enter',
        'chosen_type_death' if event=='dies' else 'chosen_type_entry_attack' if event=='enters or attacks' else 'chosen_type_entry',
        match['body'],'fixed-counter-chosen-type-zone-trigger-v1','trigger-event-normalized-zone-change',
        condition=condition,capabilities=caps)
