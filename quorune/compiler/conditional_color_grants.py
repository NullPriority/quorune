from __future__ import annotations

"""Closed self-color shell around an independently compiled quoted ability."""

import re

from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.conditional_color_grants import (
    CONDITIONAL_COLOR_GRANT_HANDLER, CONDITIONAL_COLOR_GRANT_CAPABILITY, validate_color_grant_condition,
)
from .public_state_queries import fixed_public_state_parts


def conditional_color_grant_shell(text, *, source_name):
    from .continuous_templates import _conditional_target
    parsed = fixed_public_state_parts(text, source_name=source_name)
    if parsed is None:
        return None
    condition, body = parsed
    source = SourceReferenceSpec(source_name).regex_pattern
    match = re.fullmatch(rf'(?P<subject>this creature|{source}) '
        r'(?:(?P<stats>gets [+-][0-9]+/[+-][0-9]+), )?is '
        r'(?P<color>white|blue|black|red|green),? and has (?P<abilities>.+?)\.?', body, re.I)
    if match is None:
        return None
    try:
        validate_color_grant_condition(condition.to_dict())
    except (TypeError, ValueError):
        return None
    remaining = match['subject'] + ' ' + ((match['stats'] + ' and ') if match['stats'] else '')
    compiled = _conditional_target(remaining + 'has ' + match['abilities'], source_name=source_name)
    if compiled is None or compiled[0]['kind'] != 'source':
        return None
    target, modifier, capabilities = compiled
    return 'conditional-self-color-grant-v1', {
        'handler_id': CONDITIONAL_COLOR_GRANT_HANDLER, 'schema_version': 1, 'event': 'characteristics.evaluate',
        'source_condition': condition.to_dict(), 'target': dict(target), 'modifier': dict(modifier),
        'set_colors': [{'white':'W', 'blue':'U', 'black':'B', 'red':'R', 'green':'G'}[match['color'].casefold()]],
    }, tuple(sorted({*capabilities, CONDITIONAL_COLOR_GRANT_CAPABILITY}))
