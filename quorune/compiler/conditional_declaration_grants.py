from __future__ import annotations

"""Compile declaration grants behind one independently closed public condition."""

import re
from collections.abc import Mapping

from ..ability_fragments import ability_fragment_to_dict
from ..declaration_fragments import (
    DECLARATION_COMPONENT_CAPABILITY_ID,
    DeclarationRestrictionTemplate,
)
from .public_state_queries import fixed_public_state_parts


CONDITIONAL_QUERY_DECLARATION_CAPABILITY = 'continuous.declaration.conditional_query_grant'


def conditional_characteristic_declaration_grant(text: str, *, source_name: str):
    """Compose a pure queried declaration or the existing characteristic suffix."""
    parsed = fixed_public_state_parts(text, source_name=source_name)
    if parsed is None:
        return None
    condition, body = parsed
    return (
        conditional_query_declaration_grant(condition, body, source_name=source_name)
        or _characteristic_declaration_grant(condition, body, source_name=source_name)
    )


def conditional_query_declaration_grant(condition, body: str, *, source_name: str):
    """Gate an already closed query/recipient declaration with a public condition."""
    from .declaration_nodes import _direct_declaration_grant

    direct = _direct_declaration_grant(body, source_name=source_name)
    if (direct is None or not isinstance(direct[1],Mapping)
            or direct[1]['handler_id'] != 'continuous.ability.fixed-query-grant.v1'):
        return None
    descriptor = direct[1]
    fragments = descriptor['modifier']['add_ability_fragments']
    if len(fragments) != 1:
        return None
    return (
        'continuous-conditional-query-declaration-v1',
        {
            'handler_id': 'continuous.declaration.public-state.v1',
            'schema_version': 6,
            'event': 'characteristics.evaluate',
            'source_condition': condition.to_dict(),
            'target': {'kind': 'fixed_query', **descriptor['condition'], 'types_all': []},
            'modifier': {
                'add_abilities': [], 'power': 0, 'toughness': 0,
                'add_ability_fragments': list(fragments),
            },
        },
        tuple(sorted({
            CONDITIONAL_QUERY_DECLARATION_CAPABILITY,
            DECLARATION_COMPONENT_CAPABILITY_ID,
            'continuous.characteristics.fixed_public_state',
        })),
    )


def _characteristic_declaration_grant(condition, body: str, *, source_name: str):
    from .declaration_nodes import fixed_declaration_fragment_sequence, _self_declaration_rule

    for separator in reversed(tuple(re.finditer(r',?\s+and\s+', body, re.I))):
        characteristic_body = body[:separator.start()].rstrip(', ')
        declaration_body = body[separator.end():].rstrip('.')
        fragments = fixed_declaration_fragment_sequence(
            _self_declaration_rule(declaration_body), card_name=source_name,
        )
        if len(fragments) != 1:
            continue
        fragment = fragments[0]
        if isinstance(fragment, DeclarationRestrictionTemplate) and fragment.scope not in {'self', 'source_option'}:
            continue
        characteristic_body = re.sub(r',\s*(?:has|have)\s+', ' and has ', characteristic_body, flags=re.I)
        for sentinel in ('Vigilance', 'Reach', 'Haste'):
            if sentinel.casefold() in characteristic_body.casefold():
                continue
            shell = re.sub(
                r'\b(has|have) ([^.]+)$', lambda m: m[0] + ' and ' + sentinel,
                characteristic_body, flags=re.I,
            )
            if shell == characteristic_body:
                shell += ' and has ' + sentinel
            # Reuse the original parsed condition, rather than reconstructing
            # its wording or widening the characteristic body's leaf grammar.
            from .continuous_templates import _conditional_target
            result = _conditional_target(shell, source_name=source_name)
            if result is None:
                continue
            target, modifier, required = result
            if modifier['add_abilities'].count(sentinel) != 1:
                continue
            modifier = {**modifier, 'add_abilities':[a for a in modifier['add_abilities'] if a != sentinel],
                'add_ability_fragments':[ability_fragment_to_dict(fragment)]}
            from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
            capabilities = (set(required) - set(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[sentinel])) | {
                DECLARATION_COMPONENT_CAPABILITY_ID, 'continuous.characteristics.fixed_public_state',
            }
            if not modifier['add_abilities']:
                capabilities.discard('continuous.ability.fixed_query_keyword_grant')
            return ('continuous-conditional-characteristic-declaration-v1', {
                'handler_id':'continuous.declaration.public-state.v1','schema_version':6,
                'event':'characteristics.evaluate','source_condition':condition.to_dict(),
                'target':target,'modifier':modifier,
            }, tuple(sorted(capabilities)))
    return None
