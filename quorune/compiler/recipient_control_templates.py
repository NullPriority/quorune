"""Explicit recipient roles over the existing permanent-control journal."""
import re
from ..recipient_control_model import (RECIPIENT_CONTROL_MECHANIC,recipient_control_effect,
    CHOOSE_CONTROL_RECIPIENT_OPERATION,GIVE_CONTROL_SET_OPERATION)
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type
from .direct_target import direct_permanent_target_spec


def recipient_control_effect_template(text,*,card_name,source_is_permanent=None):
    handoff=_target_mana_value_handoff(text) or _chosen_other_player_handoff(text)
    if handoff is not None:return handoff
    match=re.fullmatch(r'(?P<recipient>target (?:player|opponent)|an opponent) gains control of (?P<subject>.+?)\.?',text.strip(),re.I)
    if match is None:return None
    recipient=match['recipient'].casefold();subject=match['subject'];opponent='opponent' in recipient
    permanent=direct_permanent_target_spec(subject) if subject.casefold().startswith('target ') else None
    source=source_is_permanent is True and bool(source_self_permanent_type(subject) or SourceReferenceSpec(card_name).matches(subject))
    if subject.casefold()=='all other permanents you control' and source_is_permanent is True and recipient!='an opponent':
        player={'zones':['player'],'categories':['player'],'player_relation':'opponent' if opponent else 'any','count':1}
        return ('fixed-target-recipient-control-set-v1',({'op':GIVE_CONTROL_SET_OPERATION,'schema_version':1,
            'scope':'other_controlled_permanents','controller':'$target.0','exclude_source':'$source.zone_object','source':'$source'},),
            player,(RECIPIENT_CONTROL_MECHANIC,'cr-115-targets','cr-611-continuous-effects'))
    if permanent is None and not source:return None
    if recipient=='an opponent':
        if permanent is not None:
            return ('fixed-chosen-opponent-target-control-v2',({'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':2,
                'player':'$controller','card':'$target.0','opponent_only':True},),permanent.to_target_schema(),
                (RECIPIENT_CONTROL_MECHANIC,'cr-115-targets','cr-611-continuous-effects'))
        if not source:return None
        return ('fixed-chosen-opponent-source-control-v1',({'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':1,
            'player':'$controller','card':'$source.zone_object','opponent_only':True},),None,
            (RECIPIENT_CONTROL_MECHANIC,'cr-611-continuous-effects'))
    player={'zones':['player'],'categories':['player'],'player_relation':'opponent' if opponent else 'any','count':1}
    schema=player if source else {'groups':[{'id':'recipient',**player},{'id':'permanent',**permanent.to_target_schema()}]}
    return ('fixed-target-recipient-control-v1',(recipient_control_effect(card='$source.zone_object' if source else '$target.1',controller='$target.0'),),
        schema,(RECIPIENT_CONTROL_MECHANIC,'cr-115-targets','cr-611-continuous-effects'))


def _target_mana_value_handoff(text):
    match=re.fullmatch(r'Draw cards equal to the mana value of (?P<target>target [^.]+)\. An opponent gains control of that (?P<kind>permanent|artifact|creature)\.',text.strip(),re.I)
    if match is None:return None
    target=direct_permanent_target_spec(match['target'])
    if target is None:return None
    from .bound_effect_programs import _program,_target_type_is_guaranteed
    schema=target.to_target_schema()
    if not _target_type_is_guaranteed(schema,match['kind'].casefold()):return None
    from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_AMOUNT_MECHANIC
    amount=ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET,characteristic='mana_value').to_dict()
    query=('fixed-target-mana-value-draw-v1',({'op':'draw','player':'$controller','count':amount,'private':True},),schema,
        (SCALAR_AMOUNT_MECHANIC,'cr-121-drawing-a-card','cr-115-targets'))
    gift=('fixed-chosen-opponent-target-control-v2',({'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':2,
        'player':'$controller','card':'$target.0','opponent_only':True},),schema,
        (RECIPIENT_CONTROL_MECHANIC,'cr-115-targets','cr-611-continuous-effects'))
    program=_program((query,gift),schema=schema)
    return program.compiled() if program is not None else None


def _chosen_other_player_handoff(text):
    match=re.fullmatch(r'Choose another player\. That player gains control of (?P<target>target [^.]+)\. You draw a card\.',text.strip(),re.I)
    if match is None:return None
    target=direct_permanent_target_spec(match['target'])
    if target is None:return None
    from .bound_effect_programs import _program
    schema=target.to_target_schema()
    gift=('fixed-chosen-other-player-target-control-v3',({'op':CHOOSE_CONTROL_RECIPIENT_OPERATION,'schema_version':3,
        'player':'$controller','card':'$target.0','opponent_only':False},),schema,
        (RECIPIENT_CONTROL_MECHANIC,'cr-115-targets','cr-611-continuous-effects'))
    draw=('fixed-controller-draw-v1',({'op':'draw','player':'$controller','count':1,'private':True},),None,('cr-121-drawing-a-card',))
    program=_program((gift,draw),schema=schema)
    return program.compiled() if program is not None else None


def normalized_source_recipient_control_body(body):
    match=re.fullmatch(r'(?P<recipient>target (?:player|opponent)|an opponent) gains control of it\.?',body.strip(),re.I)
    return match['recipient']+' gains control of this permanent.' if match is not None else None


def source_bound_recipient_control_result(binding,body,*,card_name):
    if binding.event.value.startswith(('spell.','card.')):return None
    if not (binding.event.value.endswith('.self') or binding.variant in {
            'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_blocks_or_becomes_blocked'}):return None
    normalized=normalized_source_recipient_control_body(body)
    return recipient_control_effect_template(normalized,card_name=card_name,source_is_permanent=True) if normalized is not None else None
