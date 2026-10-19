"""Compile complete paired characteristic shells and independent quotes."""
from __future__ import annotations

import re
from ..rules.source_references import SourceReferenceSpec
from ..soulbond_model import SOULBOND_GRANT_HANDLER, SOULBOND_GRANT_CAPABILITY, SOULBOND_CAPABILITY
from .continuous_templates import (
    attached_fixed_characteristics_handler, attached_quoted_ability_text,
    attached_quoted_ability_handler,
)


def _paired_sentence(text, source_name):
    match = re.fullmatch(r'As long as (?P<source>.+?) is paired with another creature, '
        r'(?P<subject>both creatures|each of those creatures) (?P<body>.+)', text.strip(), re.I)
    if match is None or not (match['source'].casefold() == 'this creature'
                            or SourceReferenceSpec(source_name).matches(match['source'])):
        return None
    body = match['body']
    if match['subject'].casefold() == 'both creatures':
        if body.casefold().startswith('have '):
            body = 'has ' + body[5:]
        elif body.casefold().startswith('get '):
            body = 'gets ' + body[4:]
        else:
            return None
    return 'Equipped creature ' + body


def _paired(compiled):
    if compiled is None:
        return None
    _template, raw, caps = compiled
    descriptor = {**raw, 'handler_id':SOULBOND_GRANT_HANDLER,
                  'condition':{'relation':'source_pair', 'types_all':['creature']}}
    return ('continuous-soulbond-paired-grant-v1', descriptor,
            tuple(sorted({SOULBOND_GRANT_CAPABILITY, SOULBOND_CAPABILITY,
                          *(cap for cap in caps if cap != 'continuous.attached.fixed_characteristics')})))


def paired_characteristics_handler(text, *, source_name):
    sentence = _paired_sentence(text, source_name)
    return _paired(attached_fixed_characteristics_handler(sentence, source_name=source_name)) if sentence else None


def paired_quoted_text(text, *, source_name):
    sentence = _paired_sentence(text, source_name)
    return attached_quoted_ability_text(sentence, source_name=source_name) if sentence else None


def paired_quoted_handler(text, *, fragment, fragment_capabilities, source_name):
    sentence = _paired_sentence(text, source_name)
    return _paired(attached_quoted_ability_handler(sentence, fragment=fragment,
        fragment_capabilities=fragment_capabilities, source_name=source_name)) if sentence else None
