from __future__ import annotations

"""Bounded Boolean composition of independently closed public-state leaves."""

import re

from ..continuous_conditions import FixedPublicStateConditionKind,FixedPublicStateConditionSpec


def compose_public_conditions(text: str, *, compile_atomic, source_pattern: str):
    pattern=rf'\s+(and|or)\s+(?=(?:you\b|your\b|an opponent\b|a player\b|there\b|enchanted\b|equipped\b|{source_pattern}\b))'
    parts=re.split(pattern,text.strip().rstrip('.'),flags=re.I)
    operators=parts[1::2];texts=parts[::2]
    if not 2<=len(texts)<=4 or len(set(op.casefold() for op in operators))!=1:return None
    leaves=tuple(compile_atomic(term) for term in texts)
    if any(leaf is None for leaf in leaves):return None
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.ALL if operators[0].casefold()=='and'
        else FixedPublicStateConditionKind.ANY,schema_version=6,terms=leaves)


def public_condition_parts_result(condition,body):
    if condition is None:return None
    leaves=condition.leaf_conditions()
    if any(leaf.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS for leaf in leaves):return None
    objects=[leaf for leaf in leaves if leaf.kind in {FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST}]
    if re.match(r'^it\b',body,re.I) and any(leaf.kind is FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY for leaf in objects) and len(objects)>1:return None
    return condition,body
