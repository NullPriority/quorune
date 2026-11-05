"""Closed token-creation and creation/sacrifice occurrence subscriptions."""
import re
from ..token_creation_batch_model import (TOKEN_CREATED_EVENT,TOKEN_CREATION_BATCH_EVENT,
    TOKEN_CREATION_TRIGGER_CAPABILITY)
from ..rules.event_subscriptions import FixedEventSubscription,fixed_event_subscription_condition
from .fixed_public_event_trigger_bindings import _spec

TOKEN_CREATION_VARIANTS=frozenset({'controller_creates_token','controller_creates_blood_token',
    'controller_creates_token_or_sacrifices_token','controller_creates_one_or_more_tokens',
    'controller_creates_one_or_more_creature_tokens','controller_creates_first_one_or_more_tokens'})


def token_creation_event_binding_spec(text,*,card_name=None):
    del card_name
    match=re.fullmatch(r'Whenever you create or sacrifice a token, (?P<body>.+)',text,re.I)
    if match is not None:
        subscriptions=(
            FixedEventSubscription(TOKEN_CREATED_EVENT,{'field':'owner','op':'eq','value':'$source.controller'}),
            FixedEventSubscription('permanent.sacrificed',{'all':[
                {'field':'previous_controller','op':'eq','value':'$source.controller'},
                {'field':'token','op':'truthy','value':True}]}))
        return _spec(TOKEN_CREATED_EVENT,'controller_creates_token_or_sacrifices_token',match['body'],
            'fixed-token-creation-or-sacrifice-trigger-v1','trigger-event-normalized-public-action',
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=(TOKEN_CREATION_TRIGGER_CAPABILITY,'trigger.subscription.fixed_multi_event','trigger.event.normalized_zone_change'))
    match=re.fullmatch(r'Whenever you create (?P<quantity>a token|a Blood token|one or more creature tokens|one or more tokens)(?P<first> for the first time each turn)?, (?P<body>.+)',text,re.I)
    if match is None:return None
    quantity=match['quantity'].casefold();first=bool(match['first'])
    if first and quantity!='one or more tokens':return None
    if quantity in {'a token','a blood token'}:
        condition={'field':'owner','op':'eq','value':'$source.controller'}
        if quantity=='a blood token':condition={'all':[condition,
            {'field':'types','op':'contains_any','value':['artifact']},
            {'field':'subtypes','op':'contains_any','value':['blood']}]}
        return _spec(TOKEN_CREATED_EVENT,'controller_creates_blood_token' if quantity=='a blood token' else 'controller_creates_token',
            match['body'],'fixed-token-creation-trigger-v1','trigger-event-normalized-public-action',
            condition=condition,capabilities=(TOKEN_CREATION_TRIGGER_CAPABILITY,))
    conditions=[{'field':'creator','op':'eq','value':'$source.controller'},
        {'field':'creature_quantity' if quantity=='one or more creature tokens' else 'quantity','op':'gte','value':1}]
    if first:conditions.append({'field':'first_creation_this_turn','op':'eq','value':True})
    variant=('controller_creates_first_one_or_more_tokens' if first else 'controller_creates_one_or_more_creature_tokens'
        if quantity=='one or more creature tokens' else 'controller_creates_one_or_more_tokens')
    return _spec(TOKEN_CREATION_BATCH_EVENT,variant,match['body'],'fixed-token-creation-batch-trigger-v1',
        'trigger-event-normalized-public-action',condition={'all':conditions},capabilities=(TOKEN_CREATION_TRIGGER_CAPABILITY,))
