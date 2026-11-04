"""Capability closure for fixed atomic instructions sharing one player target."""
from ..compiler.target_player_sequences import TARGET_PLAYER_SEQUENCE_CAPABILITY,TARGET_PLAYER_SEQUENCE_MECHANIC
from collections.abc import Mapping


def _target_references(value):
    if isinstance(value,str):return {value} if value.startswith('$target.') else set()
    if isinstance(value,Mapping):return set().union(*(_target_references(child) for child in value.values()))
    if isinstance(value,(list,tuple)):return set().union(*(_target_references(child) for child in value))
    return set()


def target_player_sequence_node_capabilities(*,effects,target_schema,mechanic_ids):
    mechanics=set(mechanic_ids)
    if TARGET_PLAYER_SEQUENCE_MECHANIC not in mechanics or not 2<=len(effects)<=4:return ()
    if target_schema not in ({'zones':['player'],'categories':['player'],'count':1,'player_relation':'any'},
            {'zones':['player'],'categories':['player'],'count':1,'player_relation':'opponent'}):return ()
    if any(effect.get('op') not in {'draw','mill','life','lose_life','choose_cards_apnap','place_player_counters'} for effect in effects):return ()
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    dependencies={TARGET_PLAYER_SEQUENCE_CAPABILITY,'target.revalidate_resolution'}
    targeted=False
    for effect in effects:
        targets=_target_references(effect)
        if targets-{'$target.0'}:return ()
        controller=effect.get('op')=='life' and effect.get('player')=='$controller'
        if not controller and targets!={'$target.0'}:return ()
        targeted|=not controller
        child_schema=None if controller else target_schema
        if effect.get('op')=='place_player_counters' and target_schema['player_relation']=='any':child_schema={k:v for k,v in target_schema.items() if k!='player_relation'}
        caps=closed_effect_component_capabilities((effect,),target_schema=child_schema,
            mechanics=mechanics-{TARGET_PLAYER_SEQUENCE_MECHANIC})
        if not caps:return ()
        dependencies.update(caps)
    return tuple(sorted(dependencies)) if targeted else ()


def target_player_sequence_runtime_dependencies(program):
    if TARGET_PLAYER_SEQUENCE_MECHANIC not in program.coverage:return ()
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies
    scoped=composed_spell_family_runtime_dependencies(program,capability=TARGET_PLAYER_SEQUENCE_CAPABILITY,
        mechanic=TARGET_PLAYER_SEQUENCE_MECHANIC)
    if scoped is not None:return scoped
    from .capabilities import capability_dependencies_for_node
    # Runtime annotations are not printed mechanic inputs. The canonical owner
    # projects amounts and decodes modal/conditional children in their own scope.
    mechanics=set(program.coverage)-{'generated_oracle_ir','spell_resolution'}
    required=capability_dependencies_for_node(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=mechanics,cost_schema=getattr(program,'cost_schema',None))
    if TARGET_PLAYER_SEQUENCE_CAPABILITY not in required:
        raise ValueError('Shared player sequence lacks its closed child shapes and one target schema')
    return required


def is_closed_target_player_sequence_program(program):
    try:required=target_player_sequence_runtime_dependencies(program)
    except (ValueError,TypeError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)
