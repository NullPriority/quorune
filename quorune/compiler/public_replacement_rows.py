from __future__ import annotations

"""Retain raw spans while joining a proved spell self-replacement pair."""

import re
from .ir_model import SourceSpan
from .public_self_replacement_templates import normalize_public_replacement_text


def public_replacement_material_rows(rows, *, oracle_text, source_name, compile_effect):
    result=[]
    for row in rows:
        if not result or not re.search(r'\binstead\b',row[1],re.I):
            result.append(row);continue
        previous=result[-1]
        combined=normalize_public_replacement_text(previous[1]+' '+row[1])
        compiled=compile_effect(combined,card_name=source_name)
        if compiled[0] not in {'fixed-public-self-replacement-v1','public-same-target-self-replacement-v1','public-source-self-replacement-v1'}:
            result.append(row);continue
        span=SourceSpan(previous[2].start,row[2].end,previous[2].line)
        result[-1]=(oracle_text[span.start:span.end],combined,span)
    return result
