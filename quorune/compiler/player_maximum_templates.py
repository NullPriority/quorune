"""Matching unique-maximum intervening conditions and control recipients."""
from . import regex_working_set as re
from ..player_maximum_model import PlayerMaximumSpec,PLAYER_MAXIMUM_MECHANIC
from ..continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind,FixedPublicStateFact
from ..recipient_control_model import recipient_control_effect,RECIPIENT_CONTROL_MECHANIC
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type
from ..creature_subtypes import canonical_creature_subtype_surface


def _metric(verb,subject):
    subject=subject.casefold()
    if verb in {'has','with'}:
        return {'life':'life','cards in hand':'hand_count'}.get(subject)
    if subject=='creatures':return 'creature_count'
    if subject=='lands':return 'land_count'
    subtype=canonical_creature_subtype_surface(subject)
    return 'subtype:'+subtype if subtype else None


def player_maximum_condition(text):
    match=re.fullmatch(r'a player (?P<verb>has|controls) more (?P<subject>.+?) than each other player',text.strip(),re.I)
    if match is None:return None
    metric=_metric(match['verb'].casefold(),match['subject'])
    if metric is None:return None
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        amount=1,schema_version=4,fact=FixedPublicStateFact.UNIQUE_PLAYER_MAXIMUM,fact_parameter=metric)


def opponent_more_metric_condition(text):
    match=re.fullmatch(r'an opponent (?P<verb>has|controls) more (?P<subject>.+?) than you',text.strip(),re.I)
    if match is None:return None
    metric=_metric(match['verb'].casefold(),match['subject'])
    if metric is None:return None
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        amount=1,schema_version=4,fact=FixedPublicStateFact.OPPONENT_HAS_GREATER_METRIC,fact_parameter=metric)


def player_maximum_control_result(binding,body,*,card_name):
    condition=binding.public_state_condition
    if (not isinstance(condition,FixedPublicStateConditionSpec) or condition.fact is not FixedPublicStateFact.UNIQUE_PLAYER_MAXIMUM
            or binding.active_zone!='battlefield' or binding.event.value!='step.begin' or binding.variant!='your upkeep'):return None
    match=re.fullmatch(r'the player (?:with|who (?P<verb>has|controls)) the most (?P<metric>.+?) gains control of (?P<source>.+?)\.',body.strip(),re.I)
    if match is None:return None
    metric=_metric(match['verb'].casefold() if match['verb'] else 'with',match['metric'])
    if metric!=condition.fact_parameter:return None
    if not (source_self_permanent_type(match['source']) or SourceReferenceSpec(card_name).matches(match['source'])):return None
    return ('fixed-unique-maximum-source-control-v1',(recipient_control_effect(card='$source.zone_object',
        controller=PlayerMaximumSpec(metric).to_reference()),),None,
        (RECIPIENT_CONTROL_MECHANIC,PLAYER_MAXIMUM_MECHANIC,'cr-611-continuous-effects'))
