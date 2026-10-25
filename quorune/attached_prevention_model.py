from __future__ import annotations

"""Closed current-or-last-known attachment binding for resolved shields."""

from collections.abc import Mapping
from .attachment_references import AttachmentReferenceSpec, ATTACHMENT_INFORMATION_CAPABILITY

ATTACHED_PREVENTION_CAPABILITY = 'damage.prevention.attached_recipient'
BOUND_SUBJECT_VERSION = 'subject_binding_version'


def validate_bound_prevention_version(value):
    if type(value) is not int or value != 1:
        raise ValueError('Attached prevention subjects require binding version one')


def attached_prevention_dependencies(value):
    if not isinstance(value, Mapping) or value.get('op') != 'create_damage_prevention_shield':
        return ()
    subject=value.get('subject')
    if BOUND_SUBJECT_VERSION not in value and not (isinstance(subject,Mapping) and subject.get('kind')=='source_attachment'):
        return ()
    validate_bound_prevention_version(value.get(BOUND_SUBJECT_VERSION))
    reference=AttachmentReferenceSpec.from_dict(subject)
    fields={'op','source','subject','mode','duration',BOUND_SUBJECT_VERSION}
    if value.get('mode')=='amount':
        fields.add('amount')
        if type(value.get('amount')) is not int or value['amount']<1:
            raise ValueError('Attached amount shields require a fixed positive amount')
    elif value.get('mode')!='next_instance':
        raise ValueError('Attached prevention shield mode is unsupported')
    if (set(value)!=fields or value.get('source')!='$source'
            or value.get('duration')!='until_end_of_turn'
            or reference.required_card_type!='creature' or reference.schema_version!=2):
        raise ValueError('Attached prevention has a closed source-relative creature schema')
    return (ATTACHED_PREVENTION_CAPABILITY, ATTACHMENT_INFORMATION_CAPABILITY)
