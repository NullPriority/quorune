"""Independent fraction, affected-player set and canonical damage requirements."""
from collections.abc import Mapping
from ..fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup
from ..per_player_life_damage_model import (
    PerPlayerLifeDamageSpec, BoundPerPlayerLifeDamage,
    PER_PLAYER_LIFE_DAMAGE_KIND as KIND, BOUND_PER_PLAYER_LIFE_DAMAGE_KIND as BOUND_KIND,
    PER_PLAYER_LIFE_DAMAGE_CAPABILITY as CAP,
)


def per_player_life_damage_value_dependencies(value):
    if not isinstance(value, Mapping) or value.get('kind') not in {KIND, BOUND_KIND}:
        return ()
    (PerPlayerLifeDamageSpec if value['kind'] == KIND else BoundPerPlayerLifeDamage).from_dict(value)
    return (CAP, 'quantity_expression.current_player_value', 'damage.batch.fixed_set')


def per_player_life_damage_spec(effect):
    if (not isinstance(effect, Mapping) or set(effect) != {'op', 'source', 'amount', 'groups'}
            or effect.get('op') != 'damage_fixed_set' or effect.get('source') != '$source'):
        raise ValueError('Life-fraction damage requires one complete self-source instruction')
    spec = PerPlayerLifeDamageSpec.from_dict(effect['amount'])
    groups = FixedDamageSetSpec.from_dict({'groups': effect['groups']})
    if groups.groups != (PlayerDamageGroup(spec.relation),):
        raise ValueError('Life-fraction quantity and affected-player relation differ')
    return spec


def _contains_life_damage_amount(value):
    if isinstance(value, Mapping):
        return value.get('kind') in {KIND, BOUND_KIND} or any(_contains_life_damage_amount(v) for v in value.values())
    return isinstance(value, (tuple, list)) and any(_contains_life_damage_amount(v) for v in value)


def per_player_life_damage_node_capabilities(*, effects, target_schema, mechanic_ids=(), cost_schema=None, **kwargs):
    if len(effects) != 1 or target_schema is not None or _contains_life_damage_amount(cost_schema):
        return ()
    try:
        per_player_life_damage_spec(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    from .capabilities import capability_dependencies_for_node
    base = capability_dependencies_for_node(effects=({**effects[0], 'amount': 1},), target_schema=None,
        mechanic_ids=('cr-120-damage', 'fixed-simultaneous-damage'), cost_schema=cost_schema)
    return tuple(sorted({*base, *per_player_life_damage_value_dependencies(effects[0]['amount'])})) if base else ()


def per_player_life_damage_program_dependencies(program):
    if not _contains_life_damage_amount((program.effects, getattr(program, 'handlers', ()),
            program.target_schema, getattr(program, 'cost_schema', None))):
        return ()
    spell = program.event == 'resolve' and program.active_zone == 'stack' and str(program.ability_id).startswith('spell:')
    activated = program.event == 'activate' and program.active_zone == 'battlefield'
    if not (spell or activated):
        raise ValueError('Life-fraction damage requires its ordinary spell or battlefield activation frame')
    required = per_player_life_damage_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage, cost_schema=getattr(program, 'cost_schema', None))
    if not required:
        raise ValueError('Life-fraction damage requires its complete quantity and simultaneous player set')
    return required


def is_closed_per_player_life_damage_program(program):
    try:
        required = per_player_life_damage_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bound_per_player_life_damage_recipients(effect, *, context):
    bound = BoundPerPlayerLifeDamage.from_dict(effect['amount'])
    if bound.controller != context.actor:
        raise ValueError('Life-fraction damage lost its locked effect controller')
    groups = FixedDamageSetSpec.from_dict({'groups': effect.get('groups')})
    if groups.groups != (PlayerDamageGroup(bound.relation),):
        raise ValueError('Bound life-fraction damage lost its player relation')
    context.query.require_active_seat(bound.controller)
    for recipient in bound.recipients:
        context.query.require_active_seat(recipient.ref)
    return bound.total, bound.recipients
