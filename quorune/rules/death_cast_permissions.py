"""Current chosen graveyard permission and canonical entry-counter delegation."""
from copy import deepcopy
import hashlib
from ..death_cast_permission_model import (DEATH_CAST_PERMISSION_HANDLER,DEATH_CAST_CHOICE_FIELD,
    DeathCastPermissionSpec,death_cast_choice,validate_death_cast_choice)
from ..util import stable_json


def compiled_qualified_death_specs(host,seat,card):
    from .graveyard_payment_sources import compiled_graveyard_payment_specs
    from ..public_history_query_model import PublicHistoryQuerySpec,public_history_query_value
    found=compiled_graveyard_payment_specs(host,seat,card,DEATH_CAST_PERMISSION_HANDLER)
    result=[]
    for spec in found:
        query=PublicHistoryQuerySpec('permanent_died','any_player',types_all=('creature',),schema_version=5,
            excluded_name=spec.excluded_name,excluded_subtype=spec.excluded_subtype)
        value=public_history_query_value(host.state,seat,query)
        if value is not None and value>=1:result.append(spec)
    return tuple(result)


def qualified_death_zone_permission(host,seat,card):
    return bool(compiled_qualified_death_specs(host,seat,card))


def _choice_id(choice,base_id):
    return 'qualified-death:'+hashlib.sha256(stable_json({'choice':choice,'base':base_id}).encode('utf-8')).hexdigest()[:24]


def with_qualified_death_choices(host,seat,card,options,*,force_without_mana_cost=False):
    if card.zone!='graveyard' or force_without_mana_cost:return options
    specs=compiled_qualified_death_specs(host,seat,card)
    if not specs:return options
    from ..compiled_flashback import compiled_ordinary_zone_cast_permission
    independent=compiled_ordinary_zone_cast_permission(host,seat,card,include_qualified_death=False)
    result=[]
    for option in options:
        own_method=option.get('source_zone')=='graveyard' or option.get('fixed_cast_lifecycle') is not None
        if independent or own_method:result.append(option)
        if own_method:continue
        for spec in specs:
            choice=death_cast_choice(spec);base=option['id']
            marked={**deepcopy(option),DEATH_CAST_CHOICE_FIELD:{**choice,'base_option':base},
                'id':_choice_id(choice,base),'source_zone':'graveyard',
                'label':'Cast from graveyard with an entry counter'}
            result.append(marked)
    return result


def underlying_qualified_death_option(option,current_id):
    raw=option.get(DEATH_CAST_CHOICE_FIELD)
    if raw is None:return current_id
    if not isinstance(raw,dict) and not hasattr(raw,'items'):raise ValueError('Chosen death casting price is malformed')
    if set(raw)!={'schema_version','source_zone','permission','base_option'} or type(raw['base_option']) is not str:
        raise ValueError('Chosen death casting price has a closed shape')
    choice={k:v for k,v in raw.items() if k!='base_option'}
    validate_death_cast_choice(choice)
    if current_id!=_choice_id(choice,raw['base_option']):raise ValueError('Chosen death price identity changed')
    return raw['base_option']


def validate_qualified_death_option(host,seat,card,option,*,origin):
    raw=option.get(DEATH_CAST_CHOICE_FIELD)
    if raw is None:return
    underlying_qualified_death_option(option,option['id'])
    spec=validate_death_cast_choice({k:v for k,v in raw.items() if k!='base_option'})
    if origin!='graveyard' or option.get('source_zone')!='graveyard' or spec not in compiled_qualified_death_specs(host,seat,card):
        raise ValueError('Chosen intrinsic death permission is no longer available')


def qualified_death_stack_context(option):
    raw=option.get(DEATH_CAST_CHOICE_FIELD)
    if raw is None:return {}
    choice={k:deepcopy(v) for k,v in raw.items() if k!='base_option'}
    validate_death_cast_choice(choice)
    return {DEATH_CAST_CHOICE_FIELD:choice}


def qualified_death_entry_counters(host,item):
    raw=item.context.get(DEATH_CAST_CHOICE_FIELD)
    if raw is None or item.kind=='spell_copy':return ()
    spec=validate_death_cast_choice(raw)
    if item.kind!='spell' or item.context.get('cast_origin')!='graveyard':
        raise ValueError('Qualified death entry rider requires its actual cast spell and origin')
    if not spec.entry_counters:return ()
    from ..entry_counter_model import EffectEntryCounter
    card=host.state.cards.get(item.card_object_id)
    if card is None or card.zone!='stack':raise ValueError('Entry rider lost its actual stack object')
    return (EffectEntryCounter('+1/+1',spec.entry_counters,item.controller,card.ref,'400.7h'),)
