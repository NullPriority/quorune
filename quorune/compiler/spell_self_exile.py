from __future__ import annotations

"""Consume a terminal spell move without changing body ownership."""

from dataclasses import replace
import re

from ..rules.source_references import SourceReferenceSpec
from ..rules.spell_self_exile import SELF_BOTTOM, SELF_EXILE, SELF_SHUFFLE, terminal_spell_move
from .closed_effect_programs import _top_level_positions


_SELF_EXILE = re.compile(r'Exile (?P<source>.+?)\.', re.I)
_SELF_SHUFFLE = re.compile(r"Shuffle (?P<source>.+?) into its owner['’]s library\.", re.I)

_SELF_BOTTOM = re.compile(r"Put (?P<source>.+?) on the bottom of its owner['’]s library\.", re.I)


def _terminal_body(text, *, source_name, instruction_pattern, allow_spell_reference=False):
    material = text.strip()
    positions = _top_level_positions(material, '. ')
    if not positions:
        return None
    boundary = positions[-1]
    match = instruction_pattern.fullmatch(material[boundary + 2:])
    if match is None:
        return None
    subject = match['source']
    named_spell = allow_spell_reference and subject.casefold() == 'this spell'
    if not named_spell and not SourceReferenceSpec(source_name).matches(subject):
        return None
    return material[:boundary + 1]


def terminal_self_exile_body(text, *, source_name):
    return _terminal_body(text, source_name=source_name, instruction_pattern=_SELF_EXILE)


def terminal_self_shuffle_body(text, *, source_name):
    return _terminal_body(
        text, source_name=source_name, instruction_pattern=_SELF_SHUFFLE, allow_spell_reference=True,
    )


def terminal_self_bottom_body(text, *, source_name):
    return _terminal_body(
        text, source_name=source_name, instruction_pattern=_SELF_BOTTOM, allow_spell_reference=True,
    )


def terminal_spell_move_effect_compiler(compile_effect, *, source_name, oracle_text, ordinary_spell):
    if not ordinary_spell:
        return compile_effect
    for parser, move in ((terminal_self_exile_body, SELF_EXILE), (terminal_self_shuffle_body, SELF_SHUFFLE),
                         (terminal_self_bottom_body, SELF_BOTTOM)):
        if parser(oracle_text, source_name=source_name) is not None:
            break
    else:
        return compile_effect

    def compile_spell(text, **kwargs):
        existing = compile_effect(text,**kwargs)
        if existing[0] is not None:
            return existing
        body = parser(text, source_name=source_name)
        if body is None:
            return existing
        compiled = compile_effect(body,**kwargs)
        if compiled[0] is None or not compiled[1] or not compiled[3]:
            return existing
        return (move.template+'+'+compiled[0],compiled[1],compiled[2],
                tuple(dict.fromkeys((*compiled[3],move.mechanic))))
    return compile_spell


def closed_terminal_spell_move_program(program, *, body_is_closed, price_is_closed):
    move = terminal_spell_move(program.coverage)
    if move is None or move.capability not in program.capability_dependencies:
        return False
    if (getattr(program,'event',None)!='resolve' or getattr(program,'active_zone',None)!='stack'
            or not str(getattr(program,'ability_id','')).startswith('spell:') or program.destination!=move.destination):
        return False
    template = str(program.provenance.get('template_id') or '')
    prefix = move.template+'+'
    if not template.startswith(prefix):
        # The existing price owner checks its exact descriptor and prefix first.
        return price_is_closed(program,recognizers=(body_is_closed,))
    if program.cost_schema is not None:
        from ..rules.overload_shapes import fixed_overload_program_is_closed
        return fixed_overload_program_is_closed(program)
    body = replace(program,destination='graveyard',
        provenance={**program.provenance,'template_id':template[len(prefix):]},
        coverage=[value for value in program.coverage if value!=move.mechanic])
    return body_is_closed(body)
