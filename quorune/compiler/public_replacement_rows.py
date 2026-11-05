from __future__ import annotations

"""Retain raw spans while joining proved public-condition spell clauses."""

import re
from .ir_model import SourceSpan
from .oracle_source_text import without_ability_word_label
from .public_self_replacement_templates import normalize_public_replacement_text


def public_replacement_material_rows(rows, *, oracle_text, source_name, compile_effect):
    result=[]
    for row in rows:
        material = row[1]
        tail = without_ability_word_label(material) if "Then if " in material else None
        ordered = tail is not None and tail.startswith("Then if ")
        if not result or (not ordered and not re.search(r'\binstead\b', material, re.I)):
            result.append(row)
            continue
        previous = result[-1]
        if ordered:
            combined = previous[1] + " " + tail
            templates = {"fixed-resolution-public-condition-v1"}
        else:
            combined = normalize_public_replacement_text(previous[1] + " " + material)
            templates = {
                "fixed-public-self-replacement-v1", "public-same-target-self-replacement-v1",
                "public-source-self-replacement-v1",
            }
        compiled = compile_effect(combined, card_name=source_name)
        if compiled[0] not in templates:
            result.append(row)
            continue
        span=SourceSpan(previous[2].start,row[2].end,previous[2].line)
        result[-1]=(oracle_text[span.start:span.end],combined,span)
    return result
