from __future__ import annotations

"""Independent typed-definition minimum for tokens explicitly created with Prowess."""

from typing import Mapping

from ..ability_fragments import (
    SpellCastKeywordTriggerKind, SpellCastKeywordTriggerSpec, canonical_ability_fragments,
)

TOKEN_PROWESS_CAPABILITY = 'token.creation.prowess_definition'


def token_prowess_definition_dependencies(characteristics):
    if not isinstance(characteristics, Mapping):return ()
    keywords = characteristics.get('keywords', ())
    raw = characteristics.get('ability_fragments', ())
    if not isinstance(keywords, (tuple, list)) or not isinstance(raw, (tuple, list)):
        raise ValueError('Token keyword and fragment arrays are malformed')
    fragments = canonical_ability_fragments(raw)
    prowess = [f for f in fragments if isinstance(f, SpellCastKeywordTriggerSpec)
        and f.kind is SpellCastKeywordTriggerKind.PROWESS]
    if 'Prowess' not in keywords and not prowess:return ()
    if keywords.count('Prowess') != 1 or len(prowess) != 1:
        raise ValueError('A printed token Prowess keyword requires exactly one matching typed instance')
    return (TOKEN_PROWESS_CAPABILITY, 'trigger.keyword.prowess')


def token_prowess_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('op') == 'create_token':
                required.update(token_prowess_definition_dependencies(value.get('characteristics')))
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))
