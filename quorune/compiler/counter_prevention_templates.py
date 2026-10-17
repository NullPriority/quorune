from __future__ import annotations

import re
from typing import Any, Mapping

from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type


def fixed_counter_prevention_handler(text: str, *, source_name: str
                                    ) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """One fixed self-counter result; amount-based and reflexive siblings stay residual."""
    match = re.fullmatch(
        r'If damage would be dealt to (?P<source>.+?)(?P<conditional> while it has a \+1/\+1 counter on it)?, '
        r'prevent that damage(?:\. Remove| and remove) a \+1/\+1 counter from (?P<recipient>.+?)\.?',
        text.strip(), re.IGNORECASE,
    )
    if match is None:
        return None
    refs = SourceReferenceSpec(source_name)
    if source_self_permanent_type(match['source']) is None and not refs.matches(match['source']):
        return None
    if (match['recipient'].casefold() not in {'it', 'this creature'}
            and not refs.matches(match['recipient'])):
        return None
    return ('fixed-self-counter-prevention-v1',
        {'handler_id': 'prevention.damage.fixed-self-counter.v1', 'schema_version': 1,
         'event': 'damage', 'require_counter': bool(match['conditional'])},
        ('damage.prevention.fixed_self_counter_removal', 'counter.removal.fixed_effect'))
