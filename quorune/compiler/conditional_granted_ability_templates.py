from __future__ import annotations

"""Closed public-state shells around separately compiled typed abilities."""

import re
from typing import Any, Mapping

from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_GRANTED_ABILITY_CAPABILITY_ID,
    FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
)
from .continuous_templates import (
    _ATTACHED_QUOTED_ABILITY_SENTINELS,
    _attached_ability_capabilities,
    fixed_public_state_characteristics_handler,
)


def graveyard_static_characteristics_handler(text: str, *, source_name: str):
    """CR 113.6b: explicit graveyard activity with an independent live condition."""
    match = re.fullmatch(r'As long as this card is in your graveyard and (?P<tail>.+)', text.strip(), re.I)
    if match is None:
        return None
    compiled = fixed_public_state_characteristics_handler('As long as ' + match['tail'], source_name=source_name)
    if compiled is None:
        land = re.fullmatch(r'you control (?:a|an) (?P<land>Plains|Island|Swamp|Mountain|Forest), (?P<body>.+)', match['tail'], re.I)
        if land is None:
            return None
        from .public_state_queries import fixed_characteristic_battlefield_query_subject
        from ..characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope
        from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
        from .continuous_templates import _conditional_target
        query = fixed_characteristic_battlefield_query_subject(land['land'] + ('s' if land['land'].casefold() != 'plains' else '') + ' you control')
        body = _conditional_target(land['body'], source_name=source_name)
        if query is None or body is None:
            return None
        condition = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=1,
            quantity=CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE, query=query[1]), schema_version=2)
        compiled = ('continuous-public-state-grant-v1', {'handler_id':'continuous.characteristics.fixed-public-state.v1',
            'schema_version':1, 'event':'characteristics.evaluate', 'source_condition':condition.to_dict(),
            'target':body[0], 'modifier':body[1]}, ('continuous.characteristics.fixed_public_state', *body[2]))
    if compiled is None or compiled[1]['target']['kind'] != 'fixed_query':
        return None
    if compiled[1]['modifier']['power'] or compiled[1]['modifier']['toughness']:
        return None
    descriptor = {**compiled[1], 'handler_id': 'continuous.characteristics.graveyard-public-state.v1'}
    return 'continuous-graveyard-public-state-grant-v1', descriptor, tuple(sorted({
        *compiled[2], 'continuous.ability.graveyard_static_grant',
    }))


def _conditional_quoted_ability_shell(
    oracle_line: str, *, source_name: str,
) -> tuple[str, tuple[str, Mapping[str, Any], tuple[str, ...]], str] | None:
    if oracle_line.count('"') != 2:
        return None
    start, end = oracle_line.find('"'), oracle_line.rfind('"')
    quoted = oracle_line[start + 1:end].strip()
    if not quoted or "\n" in quoted:
        return None
    for sentinel in _ATTACHED_QUOTED_ABILITY_SENTINELS:
        if sentinel.casefold() in oracle_line.casefold():
            continue
        synthetic = oracle_line[:start] + sentinel + oracle_line[end + 1:]
        # Both forms conjoin a fixed modifier and independently granted abilities.
        synthetic = re.sub(
            r", has (?P<keywords>[^,.]+), and has " + re.escape(sentinel),
            lambda match: " and has " + match.group("keywords") + " and " + sentinel,
            synthetic, flags=re.IGNORECASE,
        )
        compiled = fixed_public_state_characteristics_handler(
            synthetic, source_name=source_name,
        )
        if compiled is not None and compiled[1]["modifier"]["add_abilities"].count(sentinel) == 1:
            return quoted, compiled, sentinel
    return None


def conditional_quoted_ability_text(oracle_line: str, *, source_name: str) -> str | None:
    shell = _conditional_quoted_ability_shell(oracle_line, source_name=source_name)
    return shell[0] if shell is not None else None


def conditional_quoted_ability_handler(
    oracle_line: str, *, source_name: str,
    fragment: Mapping[str, Any], fragment_capabilities: tuple[str, ...],
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    shell = _conditional_quoted_ability_shell(oracle_line, source_name=source_name)
    if shell is None:
        return None
    _, compiled, sentinel = shell
    descriptor = dict(compiled[1])
    modifier = dict(descriptor["modifier"])
    modifier["add_abilities"] = [a for a in modifier["add_abilities"] if a != sentinel]
    modifier["add_ability_fragments"] = [dict(fragment)]
    descriptor.update(
        handler_id=FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
        schema_version=2, modifier=modifier,
    )
    capabilities = (
        set(compiled[2]) - set(_attached_ability_capabilities((sentinel,)))
    ) | set(fragment_capabilities) | {FIXED_PUBLIC_STATE_GRANTED_ABILITY_CAPABILITY_ID}
    return "continuous-public-conditional-typed-grant-v1", descriptor, tuple(sorted(capabilities))
