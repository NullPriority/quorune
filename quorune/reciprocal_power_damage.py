"""Closed reciprocal damage sequences executed by the ordinary effect cursor."""
from collections.abc import Mapping

from .creature_power_damage_model import CREATURE_POWER_DAMAGE_MECHANIC, CREATURE_POWER_DAMAGE_OPERATION
from .replacement.immutable import thaw_value

RECIPROCAL_POWER_MECHANIC = 'sequential-reciprocal-power-damage'
RECIPROCAL_POWER_CAPABILITY = 'damage.creature_power.reciprocal_sequence'


def reciprocal_power_damage_capabilities(*, effects, target_schema, mechanic_ids):
    from .rules.creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities

    mechanics = set(mechanic_ids)
    if RECIPROCAL_POWER_MECHANIC not in mechanics or len(effects) != 2:
        return ()
    first, second = effects
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return ()
    if first.get('source') != '$source' or first.get('target') != '$target.0' or first.get('kind') != 'bite':
        return ()
    if first.get('target_must_be_creature') is not True:
        return ()
    expected = {'op': CREATURE_POWER_DAMAGE_OPERATION, 'kind': 'bite', 'source': '$target.0',
        'target': '$source', 'target_must_be_creature': True, 'source_lki': None,
        'schema_version': 4, 'recipient_is_original_source': True}
    if second != expected or type(second.get('target_must_be_creature')) is not bool:
        return ()
    if type(second.get('schema_version')) is not int or second.get('recipient_is_original_source') is not True:
        return ()
    schema = thaw_value(target_schema)
    if not isinstance(schema, Mapping) or set(schema) != {'groups'}:
        return ()
    groups = schema['groups']
    if not isinstance(groups, (list, tuple)) or len(groups) != 1:
        return ()
    group = groups[0]
    if (not isinstance(group, Mapping) or group.get('types_any') != ['creature']
            or type(group.get('count')) is not int or group['count'] != 1):
        return ()
    owners = fixed_creature_power_damage_node_capabilities(effects=(first,), target_schema=schema,
        mechanic_ids=mechanics - {RECIPROCAL_POWER_MECHANIC})
    return tuple(sorted({RECIPROCAL_POWER_CAPABILITY, *owners})) if owners else ()


def reciprocal_power_damage_runtime_dependencies(program):
    def reversed_damage(value):
        if isinstance(value, Mapping):
            if (value.get('op') == CREATURE_POWER_DAMAGE_OPERATION and (value.get('schema_version') == 4
                    or value.get('target') == '$source' and str(value.get('source', '')).startswith('$target.'))):
                return True
            return any(reversed_damage(child) for child in value.values())
        return isinstance(value, (list, tuple)) and any(reversed_damage(child) for child in value)

    if not reversed_damage(program.effects):
        return ()
    owners = reciprocal_power_damage_capabilities(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=(RECIPROCAL_POWER_MECHANIC, CREATURE_POWER_DAMAGE_MECHANIC, 'cr-115-targets', 'cr-120-damage'))
    if not owners:
        raise ValueError('Reciprocal power damage requires its complete ordered source/target pair')
    return owners


def original_source_recipient_matches(host, effect):
    """Use the source context injected by the canonical semantic interpreter."""
    from .semantic_runtime.context import SemanticSourceContext

    raw = effect.get('_runtime_source')
    if not isinstance(raw, Mapping):
        raise ValueError('Original-source damage recipient requires authoritative source context')
    try:
        source = SemanticSourceContext(**raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Original-source damage recipient context is malformed') from exc
    if not source.object_id or not source.logical_object_id or effect.get('target') != source.card_ref:
        raise ValueError('Original-source damage recipient differs from the original ability source')
    card = host.state.cards.get(source.object_id)
    return card is not None and card.logical_object_id == source.logical_object_id
