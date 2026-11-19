"""Independent quantity, recipient and simultaneous-damage shape authority."""
from collections.abc import Mapping
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..fixed_damage_set_model import FixedDamageSetSpec,PlayerDamageGroup
from ..per_player_query_damage_model import (
    PerPlayerQueryDamageSpec,BoundPerPlayerQueryDamage,
    PER_PLAYER_QUERY_DAMAGE_KIND as KIND,BOUND_PER_PLAYER_QUERY_DAMAGE_KIND as BOUND_KIND,
    PER_PLAYER_QUERY_DAMAGE_CAPABILITY as CAP,
)
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY


def per_player_query_damage_value_dependencies(value):
    if not isinstance(value,Mapping):return ()
    if value.get('kind')==BOUND_KIND:
        BoundPerPlayerQueryDamage.from_dict(value)
        return (CAP,PUBLIC_QUERY_AMOUNT_CAPABILITY,'damage.batch.fixed_set')
    if value.get('kind')!=KIND:return ()
    spec=PerPlayerQueryDamageSpec.from_dict(value)
    return tuple(sorted({CAP,PUBLIC_QUERY_AMOUNT_CAPABILITY,*characteristic_quantity_capabilities(spec.amount.quantity)}))


def per_player_query_damage_spec(effect):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','amount','groups'}
            or effect.get('op')!='damage_fixed_set' or effect.get('source')!='$source'):
        raise ValueError('Per-player public damage requires one complete ordinary source instruction')
    spec=PerPlayerQueryDamageSpec.from_dict(effect['amount'])
    groups=FixedDamageSetSpec.from_dict({'groups':effect['groups']})
    if groups.groups!=(PlayerDamageGroup(spec.relation),):
        raise ValueError('Per-player damage quantity and affected-player relation differ')
    return spec


def per_player_query_damage_node_capabilities(*,effects,target_schema,mechanic_ids=(),cost_schema=None,**kwargs):
    if len(effects)!=1 or target_schema is not None or cost_schema is not None:return ()
    try:per_player_query_damage_spec(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities
    projected={**effects[0],'amount':1}
    base=fixed_damage_node_capabilities(effects=(projected,),target_schema=None,
        mechanic_ids=('cr-120-damage','fixed-simultaneous-damage'))
    if not base:return ()
    return tuple(sorted({*base,*per_player_query_damage_value_dependencies(effects[0]['amount'])}))


def per_player_query_damage_program_dependencies(program):
    found=[e for e in program.effects if isinstance(e,Mapping) and isinstance(e.get('amount'),Mapping)
        and e['amount'].get('kind') in {KIND,BOUND_KIND}]
    if not found:return ()
    if (program.event!='resolve' or program.active_zone!='stack'
            or not str(program.ability_id).startswith('spell:')):
        raise ValueError('Per-player query damage requires its represented ordinary spell frame')
    required=per_player_query_damage_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage,cost_schema=getattr(program,'cost_schema',None))
    if not required:raise ValueError('Per-player query damage requires its complete independent quantity and player batch')
    return required


def is_closed_per_player_query_damage_program(program):
    try:required=per_player_query_damage_program_dependencies(program)
    except (TypeError,ValueError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def bound_per_player_query_damage_recipients(effect,*,context):
    bound=BoundPerPlayerQueryDamage.from_dict(effect['amount'])
    if bound.controller!=context.actor:
        raise ValueError('Per-player damage lost its original effect controller')
    groups=FixedDamageSetSpec.from_dict({'groups':effect.get('groups')})
    if groups.groups!=(PlayerDamageGroup(bound.relation),):
        raise ValueError('Bound per-player damage lost its affected-player relation')
    context.query.require_active_seat(bound.controller)
    for recipient in bound.recipients:context.query.require_active_seat(recipient.ref)
    return bound.total,bound.recipients
