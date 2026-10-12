from __future__ import annotations

"""Full-consumption intrinsic copy-entry syntax with no runtime admission."""

from dataclasses import replace
import re

from ..entry_copy import EntryCopySpec
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .token_copy_templates import _fixed_exception


def entry_copy_spec(text: str, *, source_name: str) -> EntryCopySpec | None:
    match = re.fullmatch(r'You may have (?P<source>.+?) enter as a copy of '
        r'(?P<subject>.+?)(?:, except (?P<exception>.+?))?\.?', ' '.join(text.strip().split()), re.I)
    if match is None or not (source_self_permanent_type(match['source']) is not None
            or SourceReferenceSpec(source_name).matches(match['source'])):
        return None
    subject = re.sub(r'^(?:any |a |an )', '', match['subject'], flags=re.I)
    subject = re.sub(r' on the battlefield$', '', subject, flags=re.I)
    subject = re.sub(r' an opponent controls$', ' your opponents control', subject, flags=re.I)
    relation = None
    for suffix, controller in ((' you control', '$controller'), (' your opponents control', 'opponents')):
        if subject.casefold().endswith(suffix):
            subject = subject[:-len(suffix)]
            relation = controller
            break
    union = re.fullmatch(r'(?P<first>artifact|creature|enchantment) or (?P<second>artifact|creature|enchantment)', subject, re.I)
    if union is not None:
        if union['first'].casefold() == union['second'].casefold():
            return None
        query = ObjectQuerySpec(zones=('battlefield',), types_any=(union['first'], union['second']))
    else:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None or parsed[0] != 'any' or parsed[2]:
            return None
        query = parsed[1]
    if relation == '$controller':
        query = replace(query, controller='$controller')
    elif relation == 'opponents':
        query = replace(query, excluded_controllers=('$controller',))
    exception = _fixed_exception(match['exception'] or '')
    if exception is None:
        return None
    try:
        return EntryCopySpec(query, exception)
    except ValueError:
        return None
