"""A closed damage head predicates source and damaged-creature facts separately."""
import re
from ..damage_recipient_model import DAMAGE_PERMANENT_VARIANT,DAMAGE_PERMANENT_CAPABILITY
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type


def damage_permanent_binding_spec(text,*,card_name):
    match=re.fullmatch(r'(?:When|Whenever) (?P<subject>.+?) deals (?P<combat>combat )?damage to a creature, (?P<body>.+)',text,re.I)
    if match is None:return None
    from .fixed_public_event_trigger_bindings import _spec,_all_conditions,_damage_subject_condition
    subject=' '.join(match['subject'].casefold().split())
    if source_self_permanent_type(subject) is not None or card_name and SourceReferenceSpec(card_name).matches(match['subject']):
        condition=_all_conditions({'field':'source','op':'eq','value':'$source.ref'},
            {'field':'source_logical_object_id','op':'eq','value':'$source.logical_object_id'})
    else:
        condition=_damage_subject_condition(subject,card_name=card_name)
        if condition is None:return None
        if subject in {'a creature you control','an artifact creature you control'}:
            condition=_all_conditions(condition,{'field':'source_types','op':'contains_any','value':['creature']})
    return _spec('damage.dealt',DAMAGE_PERMANENT_VARIANT,match['body'],
        'fixed-counter-public-damage-trigger-v1','trigger-event-normalized-damage',
        condition=_all_conditions(condition,{'field':'target_kind','op':'eq','value':'permanent'},
            {'field':'target_types','op':'contains_any','value':['creature']},
            {'field':'combat','op':'truthy','value':True} if match['combat'] else None),
        capabilities=(DAMAGE_PERMANENT_CAPABILITY,))
