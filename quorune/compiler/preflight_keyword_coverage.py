from __future__ import annotations

"""A trusted printed keyword owner does not cover an unrelated static line."""

from .keyword_templates import keyword_mechanics


def static_lines_without_keyword_owners(lines,*,record,trusted_programs):
    covered={value for program in trusted_programs if program.trust_level=='trusted' and program.provenance.get('template_id')=='printed-keyword-list-v1'
        for value in program.coverage}
    result=[]
    for line in lines:
        mechanics=keyword_mechanics(line,record.keywords)
        if mechanics is not None and set(mechanics)<=covered:continue
        if mechanics==('increment',):
            from ..increment_model import CAPABILITY
            source_lines=record.oracle_text.splitlines()
            matches=[index+1 for index,text in enumerate(source_lines) if text.split(' (',1)[0].strip().casefold()==line.strip().casefold()]
            if any(program.trust_level=='trusted' and program.event=='spell.cast' and 'increment' in program.coverage
                    and CAPABILITY in program.capability_dependencies
                    and program.provenance.get('source_span',{}).get('line') in matches for program in trusted_programs):continue
        result.append(line)
    return result
