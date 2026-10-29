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
    fixed_query_keyword_grant_handler,
)


def conditional_typed_keyword_handler(text: str, *, source_name: str):
    """Gate an existing complete keyword modifier without discarding fragments."""
    from .public_state_queries import fixed_public_state_parts
    from .continuous_templates import _TRAILING_REMINDER, _conditional_target
    from ..continuous_conditions import FixedPublicStateConditionKind
    parts = fixed_public_state_parts(text, source_name=source_name)
    if parts is None:
        return None
    source_condition, body = parts
    body = _TRAILING_REMINDER.sub('', body.strip()).rstrip('.')
    if re.match(r'^it\b', body, re.I):
        subject = 'This creature'
        attached=next((leaf for leaf in source_condition.leaf_conditions() if leaf.kind is FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY),None)
        if attached is not None:
            subject = ('Enchanted land' if 'land' in attached.predicate.types_all
                else 'Enchanted creature')
        body = re.sub(r'^it\b', subject, body, count=1, flags=re.I)
    match = re.fullmatch(r'(?P<prefix>.+?)(?P<verb>has|have) (?P<abilities>.+)', body, re.I)
    if match is None or '"' in text:
        return None
    granted = fixed_query_keyword_grant_handler('Creatures you control have ' + match['abilities'] + '.')
    if granted is None or not granted[1]['modifier'].get('add_ability_fragments'):
        return None
    for sentinel in _ATTACHED_QUOTED_ABILITY_SENTINELS:
        compiled = _conditional_target(match['prefix'] + match['verb'] + ' ' + sentinel, source_name=source_name)
        if compiled is None:
            continue
        target, raw_modifier, body_capabilities = compiled
        modifier = dict(raw_modifier)
        if modifier['add_abilities'] != [sentinel]:
            return None
        modifier.update(granted[1]['modifier'])
        descriptor = dict(handler_id='continuous.characteristics.public-state-keywords.v1',
            schema_version=7, event='characteristics.evaluate', target=dict(target),
            modifier=modifier, source_condition=source_condition.to_dict())
        capabilities = (
            (set(body_capabilities) - _attached_ability_capabilities((sentinel,)))
            | set(granted[2]) | {'continuous.characteristics.fixed_public_state'}
        )
        if source_condition.kind is FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH:
            capabilities.add('variant.monarch.designate')
        elif source_condition.kind is FixedPublicStateConditionKind.CONTROLLER_MAX_SPEED:
            capabilities.add('variant.speed.player')
        return 'continuous-conditional-typed-keywords-v1', descriptor, tuple(sorted(capabilities))
    return None


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
    from .granted_characteristic_nodes import granted_characteristic_reference_is_closed
    static_characteristic = granted_characteristic_reference_is_closed(quoted)
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
            synthetic, source_name=source_name, attached_permanent_subject=static_characteristic,
        )
        if compiled is None:
            from .conditional_color_grants import conditional_color_grant_shell
            compiled = conditional_color_grant_shell(synthetic, source_name=source_name)
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
    if 'set_colors' in descriptor:
        from ..semantic_runtime.conditional_color_grants import CONDITIONAL_COLOR_GRANT_CAPABILITY
        descriptor['modifier'] = modifier
        grant_capability = CONDITIONAL_COLOR_GRANT_CAPABILITY
    else:
        descriptor.update(handler_id=FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
            schema_version=2, modifier=modifier)
        grant_capability = FIXED_PUBLIC_STATE_GRANTED_ABILITY_CAPABILITY_ID
    capabilities = (
        set(compiled[2]) - set(_attached_ability_capabilities((sentinel,)))
    ) | set(fragment_capabilities) | {grant_capability, FIXED_PUBLIC_STATE_GRANTED_ABILITY_CAPABILITY_ID}
    return "continuous-public-conditional-typed-grant-v1", descriptor, tuple(sorted(capabilities))
