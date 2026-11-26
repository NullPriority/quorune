"""Each sequential conditional leaf retains its complete independent closure."""
from collections.abc import Mapping
from ..compiler.sequential_public_conditions import (SEQUENTIAL_CONDITION_CAPABILITY,SEQUENTIAL_CONDITION_MECHANIC,
    SEQUENTIAL_CONDITION_TEMPLATE)
from ..compiler.closed_effect_programs import CLOSED_EFFECT_PROGRAM_MECHANIC
from ..resolution_conditions import RESOLUTION_CONDITION_OPERATION,RESOLUTION_CONDITION_MECHANIC


def sequential_condition_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (target_schema is not None or not 2<=len(effects)<=4
            or not {SEQUENTIAL_CONDITION_MECHANIC,CLOSED_EFFECT_PROGRAM_MECHANIC}<=set(mechanic_ids)):return ()
    from .resolution_condition_shapes import resolution_condition_node_capabilities
    required={SEQUENTIAL_CONDITION_CAPABILITY,'resolution.effect_program.closed_components'}
    for effect in effects:
        if (not isinstance(effect,Mapping) or effect.get('op')!=RESOLUTION_CONDITION_OPERATION
                or effect.get('prefix_mechanic_ids') or effect.get('schema_version') is not None):return ()
        leaf_mechanics=effect.get('mechanic_ids')
        if not isinstance(leaf_mechanics,(list,tuple)):return ()
        owned=resolution_condition_node_capabilities(effects=(effect,),target_schema=None,
            mechanic_ids=(RESOLUTION_CONDITION_MECHANIC,*leaf_mechanics))
        if not owned:return ()
        required.update(owned)
    return tuple(sorted(required))


def sequential_condition_runtime_dependencies(program):
    from .counter_recipient_control import counter_recipient_control_program_dependencies

    if counter_controller := counter_recipient_control_program_dependencies(program):
        return counter_controller
    effects=program.effects
    marked=getattr(program,'provenance',{}).get('template_id')==SEQUENTIAL_CONDITION_TEMPLATE
    if not marked and not (len(effects)>1 and all(e.get('op')==RESOLUTION_CONDITION_OPERATION for e in effects)):return ()
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies
    composed=composed_spell_family_runtime_dependencies(program,capability=SEQUENTIAL_CONDITION_CAPABILITY,
        mechanic=SEQUENTIAL_CONDITION_MECHANIC)
    if composed is not None:return composed
    required=sequential_condition_node_capabilities(effects=effects,target_schema=program.target_schema,
        mechanic_ids=(SEQUENTIAL_CONDITION_MECHANIC,CLOSED_EFFECT_PROGRAM_MECHANIC))
    if not required:raise ValueError('Sequential conditions lost their bounded complete leaf closure')
    return required
