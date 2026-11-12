"""Bind one revalidated target and controller to the existing damage batch."""
from collections.abc import Mapping

from ..announced_damage import AllocatedDamageRecipient
from ..fixed_dual_damage_model import (FixedTargetControllerDamageSpec,FIXED_DUAL_DAMAGE_FIELD,
    FIXED_DUAL_DAMAGE_CAPABILITY,FIXED_DUAL_DAMAGE_MECHANIC)


def fixed_dual_damage_spec(effect):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','amount',FIXED_DUAL_DAMAGE_FIELD}
            or effect['op']!='damage_fixed_set' or effect['source']!='$source'):
        raise ValueError('Fixed target/controller damage requires one complete source instruction')
    spec = FixedTargetControllerDamageSpec.from_dict(effect[FIXED_DUAL_DAMAGE_FIELD])
    if type(effect['amount']) is not int or effect['amount']!=spec.total:
        raise ValueError('Fixed target/controller damage total changed')
    return spec


def fixed_dual_damage_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or target_schema is None or FIXED_DUAL_DAMAGE_MECHANIC not in mechanic_ids:return ()
    try:spec = fixed_dual_damage_spec(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities
    required = fixed_damage_node_capabilities(effects=({'op':'damage','source':'$source',
        'target':'$target.0','amount':spec.target_amount},),target_schema=target_schema,
        mechanic_ids=set(mechanic_ids)-{FIXED_DUAL_DAMAGE_MECHANIC})
    if not required:return ()
    return tuple(sorted({*required,FIXED_DUAL_DAMAGE_CAPABILITY,'damage.batch.fixed_set','damage.result.player_life'}))


def fixed_dual_damage_payload_dependencies(effect,*,mechanics):
    if FIXED_DUAL_DAMAGE_FIELD not in effect:return ()
    fixed_dual_damage_spec(effect)
    if FIXED_DUAL_DAMAGE_MECHANIC not in mechanics:
        raise ValueError('Fixed target/controller damage lacks its compiler owner')
    return (FIXED_DUAL_DAMAGE_CAPABILITY,'damage.batch.fixed_set','damage.amount.positive',
        'damage.source.stack_current_or_lki','damage.result.player_life')


def fixed_dual_damage_runtime_dependencies(program):
    effects = tuple(effect for effect in program.effects if FIXED_DUAL_DAMAGE_FIELD in effect)
    if not effects and FIXED_DUAL_DAMAGE_MECHANIC not in program.coverage:return ()
    if not effects:raise ValueError('Fixed target/controller damage lost its represented instruction')
    required = set()
    for effect in effects:
        owned = fixed_dual_damage_node_capabilities(effects=(effect,),
            target_schema=program.target_schema,mechanic_ids=program.coverage)
        if not owned:raise ValueError('Fixed target/controller damage requires its complete target and result scope')
        required.update(owned)
    return tuple(sorted(required))


def bound_fixed_dual_damage_recipients(value,*,actor,amount):
    spec = FixedTargetControllerDamageSpec.from_dict(value,bound=True)
    if type(amount) is not int or amount!=spec.total:raise ValueError('Fixed target/controller damage total changed')
    values = value['recipients']
    if not isinstance(values,(list,tuple)) or len(values) not in (1,2):
        raise ValueError('Fixed target/controller damage requires one or two bound recipients')
    recipients = tuple(AllocatedDamageRecipient.from_dict(row) for row in values)
    controller = recipients[-1]
    if (controller.ref!=actor or controller.logical_object_id is not None
            or controller.amount!=(spec.total if len(recipients)==1 else spec.controller_amount)
            or len(recipients)==2 and (recipients[0].ref==actor or recipients[0].amount!=spec.target_amount)):
        raise ValueError('Fixed target/controller damage lost its controller or combined target share')
    return recipients


def bind_fixed_dual_damage(host,effect,item):
    if item.context.get('targets_revalidated') is not True or len(item.targets)!=1 or item.targets[0] is None:
        raise ValueError('Fixed target/controller damage requires its one revalidated target')
    value = effect.get(FIXED_DUAL_DAMAGE_FIELD)
    if isinstance(value,Mapping) and 'recipients' in value:
        recipients = bound_fixed_dual_damage_recipients(value,actor=item.controller,amount=effect.get('amount'))
        if recipients[0].ref!=item.targets[0]:
            raise ValueError('Retained fixed damage recipients differ from the original target')
        return dict(value)
    spec = fixed_dual_damage_spec(effect)
    from .damage_snapshots import recipient_snapshot
    target = recipient_snapshot(host,item.targets[0],actor=item.controller)
    if target.ref==item.controller:
        recipients = (AllocatedDamageRecipient(item.controller,None,spec.total),)
    else:
        recipients = (AllocatedDamageRecipient(target.ref,target.logical_object_id,spec.target_amount),
            AllocatedDamageRecipient(item.controller,None,spec.controller_amount))
    return {**spec.to_dict(),'recipients':[recipient.to_dict() for recipient in recipients]}
