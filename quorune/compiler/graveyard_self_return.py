"""An existing event head with a source-self graveyard-to-hand result."""
import re
from dataclasses import replace
from ..rules.source_references import SourceReferenceSpec
from ..rules.graveyard_self_return import GRAVEYARD_SELF_RETURN_CAPABILITY,GRAVEYARD_SELF_RETURN_VERSION


def _return_body(body,card_name,*,optional):
    source='this card'+('|'+SourceReferenceSpec(card_name).regex_pattern if card_name else '')
    return re.fullmatch(r'(?:you may )?return (?:'+source+r') from your graveyard to your hand\.?',body,re.I) if optional else re.fullmatch(
        r'return (?:'+source+r') from your graveyard to your hand\.?',body,re.I)


def graveyard_self_return_binding(text,*,card_name,compile_binding):
    split=re.fullmatch(r'(?P<head>.+?), (?P<body>.+)',text,re.I)
    if split is None:return None
    body=split['body']
    paid=re.fullmatch(r'you may pay (?P<mana>(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+)\. If you do, (?P<result>return .+)',body,re.I)
    if _return_body(paid['result'] if paid else body,card_name,optional=paid is None) is None:return None
    if 'one or more' in split['head'].casefold():return None
    base=compile_binding(split['head']+', draw a card.')
    if base is None or base.public_state_condition is not None:return None
    if base.event.value not in {'step.begin','spell.cast','land.enter','permanent.enter','creature.enter','artifact.enter','enchantment.enter'}:return None
    if any(word in split['head'].casefold() for word in ('enchanted','equipped','this creature','this artifact','this enchantment')):return None
    condition=base.event_condition
    def owner_relative(value):
        if isinstance(value,dict):return {key:owner_relative(child) for key,child in value.items()}
        if isinstance(value,(list,tuple)):return [owner_relative(child) for child in value]
        return '$source.owner' if value=='$source.controller' else value
    return replace(base,body=split['body'],public_active_zone='graveyard',public_condition=owner_relative(condition),
        public_mechanic=base.public_mechanic or (base.event_mechanics[0] if base.event_mechanics else 'cr-603-handling-triggered-abilities'),public_template_id='fixed-counter-graveyard-self-return-v1',
        public_capabilities=tuple(dict.fromkeys((*base.explicit_capabilities,GRAVEYARD_SELF_RETURN_CAPABILITY))),
        spell_subject=None,zone_subject=None)


def graveyard_self_return_result(binding,body,*,card_name):
    if binding.active_zone!='graveyard' or GRAVEYARD_SELF_RETURN_CAPABILITY not in binding.explicit_capabilities or _return_body(body,card_name,optional=False) is None:return None
    return ('source-graveyard-hand-v1',({'op':'self_zone_move','origin':'graveyard','destination':'hand',
        'tapped':False,'source_form':'card',GRAVEYARD_SELF_RETURN_VERSION:1},),None,('source-graveyard-hand-return',)),False
