from __future__ import annotations

"""Independent typed-definition minimum for intrinsic token trigger keywords."""

from typing import Mapping

from ..ability_fragments import (
    CombatKeywordTriggerKind, CombatKeywordTriggerSpec, SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec, canonical_ability_fragments, ability_fragment_to_dict,
)
from ..keyword_abilities import triggered_keyword_grant

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


def token_combat_keyword_definition_dependencies(characteristics):
    if not isinstance(characteristics, Mapping):return ()
    keywords,raw=characteristics.get('keywords',()),characteristics.get('ability_fragments',())
    if not isinstance(keywords,(list,tuple)) or not isinstance(raw,(list,tuple)):
        raise ValueError('Token keyword and fragment arrays are malformed')
    expected=[grant for keyword in keywords if (grant:=triggered_keyword_grant(keyword)) is not None
              and keyword!='Prowess']
    actual=[ability_fragment_to_dict(f) for f in canonical_ability_fragments(raw)
            if isinstance(f,CombatKeywordTriggerSpec) and f.kind is not CombatKeywordTriggerKind.DECAYED]
    if len(expected)!=len(actual) or any(actual.count(grant[1])!=1 for grant in expected):
        raise ValueError('A printed token combat keyword requires exactly one matching typed instance')
    return tuple(sorted({grant[2] for grant in expected}))


def token_triggered_keyword_definition_dependencies(characteristics):
    return tuple(sorted(set(token_prowess_definition_dependencies(characteristics))
                        | set(token_combat_keyword_definition_dependencies(characteristics))))


def token_combat_fragment_is_declared(fragment,keywords):
    encoded=ability_fragment_to_dict(fragment)
    return any(grant[1]==encoded for keyword in keywords
               if (grant:=triggered_keyword_grant(keyword)) is not None)


def token_prowess_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('op') == 'create_token':
                required.update(token_triggered_keyword_definition_dependencies(value.get('characteristics')))
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))
