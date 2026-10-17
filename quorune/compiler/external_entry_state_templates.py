from __future__ import annotations

"""Whole-sentence Oracle lowering for unconditional external entry tap state."""

import re

from ..external_entry_state_model import (
    EXTERNAL_ENTRY_STATE_CAPABILITY,EXTERNAL_ENTRY_STATE_HANDLER_ID,
    ExternalEntryStateSpec,PERMANENT_CARD_TYPES,
)


def external_entry_state_handler(text):
    material=' '.join(text.strip().split())
    # Ability/flavor word labels have no rules meaning (CR 207.2c-d).
    word=re.fullmatch(r"[A-Z][A-Za-z']*(?: [A-Z][A-Za-z']*)* — (?P<body>.+)",material)
    if word is not None:
        if material.casefold().startswith(('if ','when ','whenever ','as long as ','at ','this ','that ','you ','your ')):return None
        material=word['body']
    match=re.fullmatch(r'(?P<subject>[A-Za-z, ]+?)(?P<relation> your opponents control| you control)? enter tapped\.?',material,re.I)
    if match is None:return None
    subject=match['subject'].casefold()
    if subject=='permanents':types=()
    else:
        words=re.findall(r'[a-z]+',subject)
        words=[word for word in words if word!='and']
        if not words or any(word not in {kind+'s' for kind in PERMANENT_CARD_TYPES} for word in words):return None
        expected=words[0] if len(words)==1 else (', '.join(words[:-1])+(', and ' if len(words)>2 else ' and ')+words[-1])
        if subject!=expected or len(words)!=len(set(words)):return None
        types=tuple(sorted(word[:-1] for word in words))
    relation={' your opponents control':'opponents',' you control':'controller',None:'all'}[match['relation'].casefold() if match['relation'] else None]
    spec=ExternalEntryStateSpec(relation,types)
    return ('external-entry-tapped-v1',{'handler_id':EXTERNAL_ENTRY_STATE_HANDLER_ID,
        'schema_version':1,'event':'zone.change',**spec.to_dict()},EXTERNAL_ENTRY_STATE_CAPABILITY)
