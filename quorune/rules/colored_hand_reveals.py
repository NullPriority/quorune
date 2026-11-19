"""Independent result/target ownership for a colored-hand reveal instruction."""
from collections.abc import Mapping
from ..colored_hand_reveal_model import (
    COLORED_REVEAL_OPERATION as OP,COLORED_REVEAL_CAPABILITY as CAP,
    COLORED_REVEAL_MECHANIC as MECHANIC,decode_colored_hand_reveal,
)
from ..replacement.immutable import thaw_value


def colored_reveal_result_dependencies(spec):
    effect=spec.result.materialize(2);schema=thaw_value(spec.result.target_schema) if spec.result.target_schema is not None else None
    op=effect['op']
    if op=='damage':
        from .damage_capability_shapes import fixed_damage_node_capabilities
        required=fixed_damage_node_capabilities(effects=(effect,),target_schema=schema,mechanic_ids=('cr-120-damage','cr-115-targets'))
    elif op=='life':
        from .fixed_controller_effect_shapes import fixed_life_node_capabilities
        required=fixed_life_node_capabilities(effects=(effect,),target_schema=schema,mechanic_ids=('cr-119-life',))
    elif op=='modify_stats_until_end_of_turn':
        from .node_capability_shapes import fixed_target_characteristics_node_capabilities
        required=fixed_target_characteristics_node_capabilities(effects=(effect,),target_schema=schema,mechanic_ids=('cr-611-continuous-effects','cr-115-targets'))
    else:
        from .stack_controller_payment_shapes import stack_controller_payment_node_capabilities
        required=stack_controller_payment_node_capabilities(effects=(effect,),target_schema=schema,
            mechanic_ids=('counter','cr-115-targets','counter-unless-controller-payment'))
    if not required:raise ValueError('Revealed-card result lacks its existing typed owner')
    return tuple(sorted({CAP,*required}))


def colored_hand_reveal_node_capabilities(*,effects,target_schema,mechanic_ids):
    if MECHANIC not in mechanic_ids or len(effects)!=1 or effects[0].get('op')!=OP:return ()
    try:
        spec=decode_colored_hand_reveal(effects[0])
        if thaw_value(spec.result.target_schema)!=thaw_value(target_schema):return ()
        return colored_reveal_result_dependencies(spec)
    except (TypeError,ValueError,KeyError):return ()


def colored_hand_reveal_program_dependencies(program):
    found=[effect for effect in program.effects if isinstance(effect,Mapping) and effect.get('op')==OP]
    if not found:return ()
    if (len(program.effects)!=1 or len(found)!=1 or program.event!='resolve'
            or program.active_zone!='stack' or not str(program.ability_id).startswith('spell:')):
        raise ValueError('Colored hand reveal requires its ordinary resolving spell frame')
    spec=decode_colored_hand_reveal(found[0])
    if thaw_value(spec.result.target_schema)!=thaw_value(program.target_schema):raise ValueError('Reveal result target schema changed')
    return colored_reveal_result_dependencies(spec)


def is_closed_colored_hand_reveal_program(program):
    try:required=colored_hand_reveal_program_dependencies(program)
    except (ValueError,TypeError,KeyError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)


def colored_hand_reveal_payload_dependencies(value):
    if value.get('op')!=OP:return ()
    return colored_reveal_result_dependencies(decode_colored_hand_reveal(value))
