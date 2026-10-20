from __future__ import annotations

"""One source-spanned intrinsic graveyard method with fixed discard payment."""

import re

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..rules.self_graveyard_discard import CAPABILITIES, EVENT, HANDLER_ID, SelfGraveyardDiscardSpec
from .fixed_numbers import fixed_number


def self_graveyard_discard_template(text):
    match=re.fullmatch(r'You may cast this card from your graveyard by discarding '
        r'(?P<count>a|one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) cards? '
        r'in addition to paying its other costs\.',text.strip(),re.I)
    if match is None:return None
    count=1 if match['count'].casefold()=='a' else fixed_number(match['count'])
    if count is None:return None
    return 'self-graveyard-fixed-discard-v1', {'handler_id':HANDLER_ID,'schema_version':1,'event':EVENT,
        'source_zone':'graveyard','payment':SelfGraveyardDiscardSpec(count).to_dict(),
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD:True}, CAPABILITIES
