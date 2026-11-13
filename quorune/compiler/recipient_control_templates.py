"""Explicit recipient roles over the existing permanent-control journal."""
import re
from ..recipient_control_model import RECIPIENT_CONTROL_MECHANIC,recipient_control_effect,CHOOSE_CONTROL_RECIPIENT_OPERATION
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type
from .direct_target import direct_permanent_target_spec


def recipient_control_effect_template(text,*,card_name,source_is_permanent=None):
    match=re.fullmatch(r'(?P<recipient>target (?:player|opponent)|an opponent) gains control of (?P<subject>.+?)\.?',text.strip(),re.I)
    if match is None:return None
    recipient=match['recipient'].casefold();subject=match['subject'];opponent='opponent' in recipient
    permanent=direct_permanent_target_spec(subject) if subject.casefold().startswith('target ') else None
    source=source_is_permanent is True and bool(source_self_permanent_type(subject) or SourceReferenceSpec(card_name).matches(subject))
    if permanent is None and not source:return None
    if recipient=='an opponent':
        if not source:return None
        return ('fixed-chosen-opponent-source-control-v1',({'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':1,
            'player':'$controller','card':'$source.zone_object','opponent_only':True},),None,
            (RECIPIENT_CONTROL_MECHANIC,'cr-611-continuous-effects'))
    player={'zones':['player'],'categories':['player'],'player_relation':'opponent' if opponent else 'any','count':1}
    schema=player if source else {'groups':[{'id':'recipient',**player},{'id':'permanent',**permanent.to_target_schema()}]}
    return ('fixed-target-recipient-control-v1',(recipient_control_effect(card='$source.zone_object' if source else '$target.1',controller='$target.0'),),
        schema,(RECIPIENT_CONTROL_MECHANIC,'cr-115-targets','cr-611-continuous-effects'))


def normalized_source_recipient_control_body(body):
    match=re.fullmatch(r'(?P<recipient>target (?:player|opponent)|an opponent) gains control of it\.?',body.strip(),re.I)
    return match['recipient']+' gains control of this permanent.' if match is not None else None


def source_bound_recipient_control_result(binding,body,*,card_name):
    if binding.event.value.startswith(('spell.','card.')):return None
    if not (binding.event.value.endswith('.self') or binding.variant in {
            'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_blocks_or_becomes_blocked'}):return None
    normalized=normalized_source_recipient_control_body(body)
    return recipient_control_effect_template(normalized,card_name=card_name,source_is_permanent=True) if normalized is not None else None
