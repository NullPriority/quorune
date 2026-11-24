from __future__ import annotations

"""Closed non-targeting recipients for a source permanent's fixed damage."""

from . import regex_working_set as re
from ..attachment_references import AttachmentReferenceKind,AttachmentReferenceSpec
from ..event_counter_reference import EVENT_ENTRY_OBJECT
from ..rules.event_subscriptions import COMBAT_PARTICIPANT_DAMAGE_VARIANT
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type

REFERENCED_DAMAGE_CAPABILITY='damage.effect.fixed_referenced_recipient'
_DAMAGE=re.compile(r'(?P<source>.+?) deals (?P<amount>[1-9]\d*) damage to (?P<recipient>.+?)\.?',re.I)


def _parts(text,card_name):
    match=_DAMAGE.fullmatch(' '.join(text.strip().split()))
    if (match is None or (source_self_permanent_type(match['source']) is None
            and not SourceReferenceSpec(card_name).matches(match['source']))):
        return None
    return int(match['amount']),match['recipient'].casefold()


def _compiled(amount,recipient):
    return ('fixed-referenced-recipient-damage-v1',({'op':'damage','source':'$source',
        'target':recipient,'amount':amount},),None,('cr-120-damage',))


def attachment_fixed_damage_template(text,*,card_name,source_is_permanent,relation):
    if source_is_permanent is not True or relation is not AttachmentReferenceKind.ENCHANTED:
        return None
    parsed=_parts(text,card_name)
    if parsed is None:return None
    amount,recipient=parsed
    if recipient=='enchanted creature':
        target=AttachmentReferenceSpec(relation,'permanent',schema_version=2).to_dict()
    elif recipient in {"enchanted artifact's controller","enchanted artifact’s controller"}:
        from ..rules.attachment_controller_information import attachment_controller_reference
        target=attachment_controller_reference(AttachmentReferenceSpec(relation,'artifact',schema_version=2))
    else:return None
    return _compiled(amount,target)


def event_fixed_damage_template(binding,text,*,card_name):
    if binding.active_zone!='battlefield':return None
    parsed=_parts(text,card_name)
    if parsed is None or parsed[1]!='it':return None
    if binding.event.value=='creature.enter' and binding.variant=='creature:any:including_source:any_object':
        reference=EVENT_ENTRY_OBJECT
    elif binding.event.value=='creature.attacks' and binding.variant in {
        'public_attack_a_creature', COMBAT_PARTICIPANT_DAMAGE_VARIANT,
    }:
        reference='$event.permanent_object'
    else:return None
    return _compiled(parsed[0],reference)
