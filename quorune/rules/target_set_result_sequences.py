"""Require every ordered leaf to close over the same original target schema."""
from collections.abc import Mapping
from ..compiler.target_set_result_sequences import TARGET_SET_SEQUENCE_CAPABILITY,TARGET_SET_SEQUENCE_MECHANIC
from .target_characteristic_sets import OPERATION as CHARACTERISTICS

_TARGET_OPERATIONS={'tap_targets','untap_targets','place_counters_on_targets',CHARACTERISTICS}


def target_set_result_sequence_capabilities(*,effects,target_schema,mechanic_ids):
    if (not 2<=len(effects)<=4 or not isinstance(target_schema,Mapping)
            or 'count_parameter' in target_schema or effects[0].get('op') not in {'tap_targets','untap_targets'}):return ()
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from .counter_target_set_shapes import quantified_counter_target_capabilities
    from .target_characteristic_sets import target_characteristic_set_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities,fixed_scry_node_capabilities
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities
    mechanics=set(mechanic_ids)-{TARGET_SET_SEQUENCE_MECHANIC}
    required={TARGET_SET_SEQUENCE_CAPABILITY};targeted=0;independent=False
    for effect in effects:
        uses_set=effect.get('cards')=='$targets'
        if uses_set:
            if independent or effect.get('op') not in _TARGET_OPERATIONS:return ()
            targeted+=1
        else:
            if effect.get('op') not in {'draw','scry','create_token'}:return ()
            if effect.get('op')=='create_token':
                from ..fixed_token_production import clue_token_effect
                try:
                    if dict(effect)!=clue_token_effect(effect.get('quantity')):return ()
                except (ValueError,TypeError):return ()
            independent=True
        operation=effect.get('op')
        resolver=(fixed_homogeneous_target_set_node_capabilities if operation in {'tap_targets','untap_targets'}
            else quantified_counter_target_capabilities if operation=='place_counters_on_targets'
            else target_characteristic_set_capabilities if operation==CHARACTERISTICS
            else fixed_draw_node_capabilities if operation=='draw'
            else fixed_scry_node_capabilities if operation=='scry' else fixed_token_creation_node_capabilities)
        owned=resolver(effects=(effect,),target_schema=target_schema if uses_set else None,mechanic_ids=mechanics)
        if not owned:return ()
        required.update(owned)
    return tuple(sorted(required)) if targeted>=2 else ()


def is_closed_target_set_result_sequence_program(program):
    required=target_set_result_sequence_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def target_set_result_sequence_runtime_dependencies(program):
    effects=program.effects
    target_leaves=[e for e in effects if isinstance(e,Mapping) and (e.get('op') in _TARGET_OPERATIONS-{CHARACTERISTICS}
        or e.get('op')==CHARACTERISTICS and (e.get('schema_version')==6 or 'maximum_targets' in e))]
    if len(target_leaves)<2:
        if TARGET_SET_SEQUENCE_MECHANIC in program.coverage:raise ValueError('Same-set sequence marker has no shared target results')
        return ()
    # Infer primitive owners from the actual executable leaves,not coverage.
    mechanics={'fixed-homogeneous-target-set','cr-115-targets','tap-and-untap','cr-122-counters',
        'quantified-counter-target-set','cr-611-continuous-effects'}
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
    from ..keyword_counters import keyword_counter_mechanic
    for effect in effects:
        for keyword in effect.get('keywords',()):
            if keyword in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES:mechanics.add(keyword_counter_mechanic(keyword) or keyword.casefold())
        if effect.get('op')=='create_token':mechanics.update(('cr-111-tokens','fixed-token-production','investigate'))
        if effect.get('op')=='scry':mechanics.add('scry')
        if effect.get('op')=='draw':mechanics.add('cr-121-drawing-a-card')
    required=target_set_result_sequence_capabilities(effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics)
    if not required:raise ValueError('Same-set results lack their closed shared schema and primitive owners')
    return required
