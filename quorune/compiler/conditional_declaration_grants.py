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
    from .continuous_templates import bind_conditional_subject
    body=bind_conditional_subject(body,condition,attached_permanent_subject=bool(re.search(r'\benchanted permanent\b',text,re.I)))
    return (
        conditional_query_declaration_grant(condition, body, source_name=source_name)
        or _characteristic_declaration_grant(condition, body, source_name=source_name)
    )


def conditional_query_declaration_grant(condition, body: str, *, source_name: str):
    """Gate an independently closed giver-owned rule with a public condition."""
    from .declaration_nodes import _declaration_grant_target
    from .public_state_queries import fixed_characteristic_battlefield_query_subject
    from .bound_declaration_rules import bound_rule_handler
    from ..object_predicate import ObjectQuerySpec
    target=_declaration_grant_target(body,source_name=source_name)
    if target is None:return None
    relation,subject,rules=target
    if relation=='attached':
        return bound_rule_handler(scope='attached',query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)),rules=rules,condition=condition)
    controller,query,exclude=fixed_characteristic_battlefield_query_subject(subject)
    return bound_rule_handler(scope='fixed_query',query=query,rules=rules,controller=controller,exclude_source=exclude,condition=condition)


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
                'add_ability_fragments':list(modifier.get('add_ability_fragments',()))}
            from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
            capabilities = (set(required) - set(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[sentinel])) | {
                DECLARATION_COMPONENT_CAPABILITY_ID, 'continuous.characteristics.fixed_public_state',
            }
            if not modifier['add_abilities']:
                capabilities.discard('continuous.ability.fixed_query_keyword_grant')
            descriptor={
                'handler_id':'continuous.declaration.public-state.v1','schema_version':6,
                'event':'characteristics.evaluate','source_condition':condition.to_dict(),
                'target':target,'modifier':modifier,
            }
            from .bound_declaration_rules import with_bound_rule_characteristics
            return with_bound_rule_characteristics(('continuous-conditional-characteristic-declaration-v1',descriptor,
                tuple(sorted(capabilities))),fragments,condition=condition)
    return None
