from __future__ import annotations

"""Compose independently closed chosen queries and public conditions."""

import re

from ..entry_designations import (
    CONDITIONAL_CHOSEN_CHARACTERISTICS_CAPABILITY,
    CONDITIONAL_CHOSEN_CHARACTERISTICS_HANDLER_ID,
)
from .public_state_queries import fixed_public_state_parts


def _conditional_chosen_grant(condition, compiled):
    from ..semantic_runtime.conditional_chosen_characteristics import ConditionalChosenCharacteristicsHandler

    descriptor = {
        'handler_id': CONDITIONAL_CHOSEN_CHARACTERISTICS_HANDLER_ID,
        'schema_version': 1,
        'event': 'characteristics.evaluate',
        'source_condition': condition.to_dict(),
        'body': dict(compiled[1]),
    }
    try:
        ConditionalChosenCharacteristicsHandler().validate(descriptor)
    except (TypeError, ValueError):
        return None
    return (
        'fixed-conditional-chosen-designation-characteristics-v1', descriptor,
        tuple(sorted({*compiled[2], CONDITIONAL_CHOSEN_CHARACTERISTICS_CAPABILITY,
                      'continuous.characteristics.fixed_public_state'})),
    )


def conditional_chosen_characteristics_handler(text: str, *, source_name: str):
    from .entry_designation_templates import _fixed_chosen_characteristics_handler

    parsed = fixed_public_state_parts(text, source_name=source_name)
    if parsed is not None:
        condition, body = parsed
        compiled = _fixed_chosen_characteristics_handler(body)
        return None if compiled is None else _conditional_chosen_grant(condition, compiled)
    if '. ' not in text:
        return None
    prefix, tail = text.split('. ', 1)
    original = _fixed_chosen_characteristics_handler(prefix + '.')
    parsed = fixed_public_state_parts(tail, source_name=source_name)
    if original is None or parsed is None:
        return None
    subject = re.fullmatch(r'(?P<subject>.+?) (?:get|have) .+', prefix, re.IGNORECASE)
    condition, body = parsed
    continuation = re.fullmatch(r'they (?:also )?(?P<body>(?:get|have) .+)', body, re.IGNORECASE)
    if subject is None or continuation is None:
        return None
    compiled = _fixed_chosen_characteristics_handler(subject['subject'] + ' ' + continuation['body'])
    conditional = None if compiled is None else _conditional_chosen_grant(condition, compiled)
    if conditional is None:
        return None
    return (
        'fixed-chosen-designation-shared-conditional-query-v1',
        (original[1], conditional[1]),
        tuple(sorted({*original[2], *conditional[2]})),
    )
