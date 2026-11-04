"""Closed future actions preserve their independently established object role."""
import re
from ..delayed_object_model import DELAYED_OBJECT_MECHANIC,SCHEDULE_OBJECT_ACTION
from ..rules.source_references import SourceReferenceSpec
from ..damage_recipient_model import DAMAGE_PERMANENT_VARIANT,DAMAGE_PERMANENT_OBJECT


def delayed_object_result(binding,body,*,card_name):
    counter=_delayed_source_counter_result(binding,body,card_name=card_name)
    if counter is not None:return counter
    match=re.fullmatch(r'(?P<action>Destroy|Sacrifice) (?P<subject>.+?) (?P<time>at end of combat|at the beginning of the next end step)\.',body,re.I)
    if match is None:
        match=re.fullmatch(r"(?P<action>Return) (?P<subject>.+?) to its owner's hand (?P<time>at end of combat|at the beginning of the next end step)\.(?: \(Return it only if it's on the battlefield\.\))?",body,re.I)
    if match is None:return None
    subject=match['subject'].casefold()
    if subject=='that creature' and binding.variant=='source_block_pair_counterpart':reference='$event.block_counterpart'
    elif subject=='that creature' and binding.variant==DAMAGE_PERMANENT_VARIANT:reference=DAMAGE_PERMANENT_OBJECT
    elif (subject in {'this creature','this permanent','this artifact','this enchantment','this land'}
            or SourceReferenceSpec(card_name).matches(match['subject'])):reference='$source.zone_object'
    elif subject in {'it','him','her'} and (binding.event.value.endswith('.self') or binding.variant in {
            'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_attacks_or_blocks',
            'source_blocks_or_becomes_blocked'}):reference='$source.zone_object'
    else:return None
    action=match['action'].casefold();action='bounce' if action=='return' else action
    effect={'op':SCHEDULE_OBJECT_ACTION,'schema_version':1,'object':reference,'action':action,
        'timing':'end_of_combat' if match['time'].casefold()=='at end of combat' else 'next_end_step'}
    return ('fixed-delayed-object-action-v1',(effect,),None,(DELAYED_OBJECT_MECHANIC,)),False


def _delayed_source_counter_result(binding,body,*,card_name):
    match=re.fullmatch(r'(?P<body>(?:Put|Remove) .+?) (?P<time>at end of combat|at the beginning of the next end step)\.',body,re.I)
    if match is None:return None
    text=match['body']+'.'
    source_pronoun=binding.event.value.endswith('.self') or binding.variant in {
        'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_attacks_or_blocks','source_blocks_or_becomes_blocked'}
    if source_pronoun:text=re.sub(r'\b(on|from) (?:it|him|her)(?=\.)',r'\1 this permanent',text,flags=re.I)
    from .counter_placement_templates import fixed_counter_placement_effect_template
    from .counter_removal_templates import fixed_source_counter_removal_effect_template
    placement=fixed_counter_placement_effect_template(text,card_name=card_name)
    compiled=placement.compiled() if placement is not None else fixed_source_counter_removal_effect_template(text,card_name=card_name)
    if compiled is None:return None
    template,effects,schema,mechanics=compiled
    if schema is not None or len(effects)!=1 or effects[0].get('card')!='$source.zone_object':return None
    primitive=effects[0]
    effect={'op':SCHEDULE_OBJECT_ACTION,'schema_version':2,'object':'$source.zone_object',
        'action':primitive['op'],'counter':primitive['counter'],'amount':primitive['amount'],'source':'$source',
        'timing':'end_of_combat' if match['time'].casefold()=='at end of combat' else 'next_end_step'}
    return ('fixed-delayed-source-counter-v2',(effect,),None,(DELAYED_OBJECT_MECHANIC,*mechanics)),False
