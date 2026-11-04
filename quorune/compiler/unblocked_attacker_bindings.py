"""Unblocked syntax binds to the post-blocker-declaration occurrence."""
import re
from ..rules.source_references import SourceReferenceSpec
from ..unblocked_attacker_model import UNBLOCKED_ATTACKER_EVENT,UNBLOCKED_ATTACKER_CAPABILITY,UNBLOCKED_ATTACKER_VARIANT


def unblocked_attacker_binding_spec(text,*,card_name):
    match=re.fullmatch(r'(?:When|Whenever) (?P<source>.+?) attacks and isn[\x27’]t blocked, (?P<body>.+)',text,re.I)
    if match is None:return None
    if match['source'].casefold()!='this creature' and not (card_name and SourceReferenceSpec(card_name).matches(match['source'])):return None
    from .fixed_public_event_trigger_bindings import _spec
    return _spec(UNBLOCKED_ATTACKER_EVENT,UNBLOCKED_ATTACKER_VARIANT,match['body'],
        'fixed-counter-public-attack-trigger-v1','trigger-event-normalized-public-action',
        condition={'all':[{'field':'card','op':'eq','value':'$source.ref'},
            {'field':'card_object_identity','op':'eq','value':'$source.logical_object_id'}]},capabilities=(UNBLOCKED_ATTACKER_CAPABILITY,))
