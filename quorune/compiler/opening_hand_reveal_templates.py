from __future__ import annotations

import re
from ..opening_reveal_model import OpeningRevealSpec,OPENING_REVEAL_CAPABILITY,OPENING_REVEAL_HANDLER
from ..replacement.immutable import FrozenMap
from ..rules.source_references import SourceReferenceSpec


def opening_hand_reveal_permission_handler(text,*,source_name=None):
    text=' '.join(text.strip().split())
    if not text.casefold().startswith('you may reveal '):return None
    source='this card' if not source_name else rf'(?:this card|{SourceReferenceSpec(source_name).regex_pattern})'
    prefix=rf'You may reveal {source} from your opening hand\. If you do, '
    match=re.fullmatch(prefix+r'at the beginning of (?P<when>the first upkeep|your first upkeep|your first main phase of the game), (?P<body>.+)',text,re.I)
    if match is not None:
        timing={'the first upkeep':'first_upkeep','your first upkeep':'your_first_upkeep',
            'your first main phase of the game':'your_first_main'}[match['when'].casefold()]
        body=match['body']
    else:
        match=re.fullmatch(prefix+r'(?P<body>.+?) at the beginning of your first upkeep\.',text,re.I)
        if match is None:return None
        timing='your_first_upkeep';body=match['body'].rstrip('.')+'.'
    from ..oracle_ir import _effect_template
    template,effects,schema,mechanics=_effect_template(body,card_name=source_name or '')
    if template is None or schema is not None:return None
    try:spec=OpeningRevealSpec(timing,tuple(FrozenMap(e) for e in effects),tuple(mechanics))
    except (ValueError,TypeError):return None
    descriptor={'handler_id':OPENING_REVEAL_HANDLER,'schema_version':1,'event':'action.permission',
        'permission':'opening_hand_reveal','requires_complete_card_program':True,'scheduled_result':spec.to_dict()}
    return ('opening-hand-reveal-permission-v1',descriptor,tuple(sorted({OPENING_REVEAL_CAPABILITY,*spec.required_capabilities()})))
