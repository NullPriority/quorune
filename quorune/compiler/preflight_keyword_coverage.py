from __future__ import annotations

"""A trusted printed keyword owner does not cover an unrelated static line."""

from .keyword_templates import keyword_mechanics


def static_lines_without_keyword_owners(lines,*,record,trusted_programs):
    covered={value for program in trusted_programs if program.trust_level=='trusted' and program.provenance.get('template_id')=='printed-keyword-list-v1'
        for value in program.coverage}
    result=[]
    for line in lines:
        mechanics=keyword_mechanics(line,record.keywords)
        if mechanics is None or not set(mechanics)<=covered:result.append(line)
    return result
