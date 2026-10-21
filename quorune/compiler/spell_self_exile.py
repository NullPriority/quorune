from __future__ import annotations

"""Consume a terminal source-self instruction without changing body ownership."""

from dataclasses import replace
import re

from ..rules.source_references import SourceReferenceSpec
from ..rules.spell_self_exile import CAPABILITY,MECHANIC,TEMPLATE
from .closed_effect_programs import _top_level_positions


def terminal_self_exile_body(text, *, source_name):
    positions = _top_level_positions(text.strip(), '. ')
    if not positions:
        return None
    material = text.strip();boundary = positions[-1]
    instruction = material[boundary+2:]
    match = re.fullmatch(r'Exile (?P<source>.+?)\.',instruction,re.I)
    if match is None or not SourceReferenceSpec(source_name).matches(match['source']):
        return None
    body = material[:boundary+1]
    return body if body else None


def spell_self_exile_effect_compiler(compile_effect, *, source_name, oracle_text, ordinary_spell):
    if not ordinary_spell or terminal_self_exile_body(oracle_text,source_name=source_name) is None:
        return compile_effect
    def compile_spell(text, **kwargs):
        existing = compile_effect(text,**kwargs)
        if existing[0] is not None:
            return existing
        body = terminal_self_exile_body(text,source_name=source_name)
        if body is None:
            return existing
        compiled = compile_effect(body,**kwargs)
        if compiled[0] is None or not compiled[1] or not compiled[3]:
            return existing
        return (TEMPLATE+'+'+compiled[0],compiled[1],compiled[2],
                tuple(dict.fromkeys((*compiled[3],MECHANIC))))
    return compile_spell


def closed_spell_self_exile_program(program, *, body_is_closed, price_is_closed):
    if MECHANIC not in program.coverage or CAPABILITY not in program.capability_dependencies:
        return False
    if (getattr(program,'event',None)!='resolve' or getattr(program,'active_zone',None)!='stack'
            or not str(getattr(program,'ability_id','')).startswith('spell:') or program.destination!='exile'):
        return False
    template = str(program.provenance.get('template_id') or '')
    prefix = TEMPLATE+'+'
    if not template.startswith(prefix):
        # The existing price owner checks its exact descriptor and prefix first.
        return price_is_closed(program,recognizers=(body_is_closed,))
    if program.cost_schema is not None:
        return False
    body = replace(program,destination='graveyard',
        provenance={**program.provenance,'template_id':template[len(prefix):]},
        coverage=[value for value in program.coverage if value!=MECHANIC])
    return body_is_closed(body)
