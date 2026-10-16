from __future__ import annotations

"""Printed Transmute activation and retained discarded-source information."""

import math
import re
from typing import Any, Mapping

from .abilities import ActivatedAbility
from .replacement.immutable import FrozenMap
from .util import mana_cost_to_vector


TRANSMUTE_CAPABILITY = 'activation.transmute.hand'
TRANSMUTE_VALUE = '$source.discarded_mana_value'
TRANSMUTE_CONTEXT = 'transmute_discard_snapshot'
_PRINTED = re.compile(r'^Transmute\s+(?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\.?$', re.IGNORECASE)


def fixed_transmute_ability(*, material_line: str, oracle_line: str, line_index: int) -> ActivatedAbility | None:
    match = _PRINTED.fullmatch(material_line.strip())
    if match is None:
        return None
    cost = match['cost'].upper()
    mana, complex_symbols = mana_cost_to_vector(cost)
    if complex_symbols:
        return None
    return ActivatedAbility(ability_id=f'ab{line_index + 1}', line_index=line_index,
        oracle_line=oracle_line, cost_text=cost,
        effect_text='Search your library for a card with the discarded card\'s mana value, reveal it, put it into your hand, then shuffle.',
        zones=('hand',), mana=FrozenMap(mana), discard_source=True, sorcery_speed=True)


def transmute_search_effect() -> dict[str, Any]:
    return {'op': 'search', 'zone': 'library', 'selector': {'mana_value': {'equal': TRANSMUTE_VALUE}},
        'count': {'minimum': 1, 'maximum': 1}, 'destination': 'hand', 'reveal': True, 'shuffle_after': True}


def transmute_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None) -> tuple[str, ...]:
    if set(mechanic_ids) != {'transmute'} or target_schema is not None or len(effects) != 1:
        return ()
    if effects[0] != transmute_search_effect():
        return ()
    return (TRANSMUTE_CAPABILITY,)


def is_closed_transmute_program(program: Any) -> bool:
    from .semantic_runtime.activated_abilities import activated_abilities_from_descriptors
    if getattr(program, 'event', None) != 'activate' or getattr(program, 'active_zone', None) != 'hand' or TRANSMUTE_CAPABILITY not in program.capability_dependencies:
        return False
    mechanics = set(program.coverage) - {'generated_oracle_ir', 'runtime_static_handler'}
    if not transmute_node_capabilities(effects=program.effects, target_schema=program.target_schema, mechanic_ids=mechanics):
        return False
    if len(program.handlers) != 1 or program.handlers[0].get('requires_complete_card_program') is not True:
        return False
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        return False
    ability = abilities[0]
    return bool(ability.zones == ('hand',) and ability.discard_source and ability.sorcery_speed
        and not (ability.tap_source or ability.untap_source or ability.sacrifice_source or ability.exile_source
            or ability.mana_ability or ability.complex_symbols or ability.uncompiled_costs or ability.choices
            or ability.life_payment or ability.energy_payment or ability.random_discard_payment or ability.loyalty_delta))


def capture_transmute_discard(host: Any, program: Any, source: Any) -> dict[str, Any]:
    if program is None or not any(effect == transmute_search_effect() for effect in program.effects):
        return {}
    if not is_closed_transmute_program(program) or source.zone != 'hand':
        raise ValueError('Transmute requires its capability and a source in hand')
    value = host._target_snapshot(source.ref).get('mana_value')
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Transmute source mana value is unavailable')
    return {TRANSMUTE_CONTEXT: {'schema_version': 1, 'source_object_id': source.object_id,
        'source_logical_object_id': source.logical_object_id, 'mana_value': value, 'characteristics_defined': True}}


def finalize_transmute_discard(host: Any, source: Any, context: dict[str, Any]) -> None:
    if not context:
        return
    # CR701.9c: an unrevealed discard redirected into a hidden zone has
    # undefined characteristics, even though its pre-discard value was known.
    from .zone_transition_model import PUBLIC_ZONES
    defined = source.zone in PUBLIC_ZONES and not source.face_down
    context[TRANSMUTE_CONTEXT]['characteristics_defined'] = bool(
        defined or set(host.active_seats) <= set(source.revealed_to))


def retained_transmute_mana_value(item: Any) -> int | float | None:
    snapshot = item.context.get(TRANSMUTE_CONTEXT)
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        'schema_version', 'source_object_id', 'source_logical_object_id', 'mana_value', 'characteristics_defined'}:
        raise ValueError('Transmute discard snapshot is missing or malformed')
    value = snapshot['mana_value']
    if type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1 or (
        type(snapshot['source_object_id']) is not str or not snapshot['source_object_id']
        or type(snapshot['source_logical_object_id']) is not str or not snapshot['source_logical_object_id']
        or type(snapshot['characteristics_defined']) is not bool
        or snapshot['source_object_id'] != item.source_object_id
        or snapshot['source_logical_object_id'] != item.context.get('source_logical_object_id')
        or type(value) not in (int, float) or not math.isfinite(value) or value < 0
    ):
        raise ValueError('Transmute discard snapshot identity or mana value is invalid')
    return value if snapshot['characteristics_defined'] else None
