"""Require every ordered leaf to close over the same original target schema."""
from collections.abc import Mapping
from ..replacement.immutable import FrozenMap, thaw_value
from ..compiler.target_set_result_sequences import (
    TARGET_SET_SEQUENCE_CAPABILITY,TARGET_SET_SEQUENCE_MECHANIC,CHARACTERISTIC_TAIL_OPERATIONS,
)
from ..tap_state import TAP_TARGET_SET_UNTAP_FIELD
from .target_characteristic_sets import OPERATION as CHARACTERISTICS, exact_optional_creature_vehicle_keyword_grant

_TARGET_OPERATIONS={'tap_targets','untap_targets','place_counters_on_targets',CHARACTERISTICS}


def _characteristic_tail_capabilities(effect,mechanics):
    operation=effect.get('op')
    if operation=='create_token':
        from .token_creation_capability_shapes import _fixed_token_effect_closure
        closed=_fixed_token_effect_closure(effect)
        if closed is None:
            return ()
        required,represented=closed
        return required if represented<=mechanics else ()
    from .fixed_controller_effect_shapes import fixed_life_node_capabilities
    from .amass_capability_shapes import fixed_amass_node_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities,fixed_scry_node_capabilities
    resolver=(fixed_draw_node_capabilities if operation=='draw' else fixed_scry_node_capabilities if operation=='scry'
        else fixed_life_node_capabilities if operation in {'life','lose_life'} else fixed_amass_node_capabilities)
    return resolver(effects=(effect,),target_schema=None,mechanic_ids=mechanics)


_OPTIONAL_CREATURE_VEHICLE_COUNTERS = {
    "op": "place_counters_on_targets", "schema_version": 2, "cards": "$targets",
    "maximum_targets": 1, "source": "$source", "counter": "+1/+1", "amount": 2,
}


_OPTIONAL_CREATURE_FIRST_STRIKE_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "up_to": 1,
}
_OPTIONAL_CREATURE_FIRST_STRIKE_RESULT = {
    "op": CHARACTERISTICS, "schema_version": 6, "cards": "$targets", "maximum_targets": 1,
    "power": 0, "toughness": 0, "keywords": ["First Strike"],
}


def _optional_counter_keyword_sequence_capabilities(effects, target_schema, mechanics):
    from .counter_target_set_shapes import quantified_counter_target_capabilities
    from .target_characteristic_sets import target_characteristic_set_capabilities

    if len(effects) != 2 or not isinstance(target_schema, Mapping) or type(target_schema.get("up_to")) is not int:
        return ()
    counter, keywords = effects
    if type(counter.get("amount")) is not int:
        return ()
    if exact_optional_creature_vehicle_keyword_grant(keywords, target_schema):
        expected_counter = _OPTIONAL_CREATURE_VEHICLE_COUNTERS
    elif (thaw_value(FrozenMap(target_schema)) == _OPTIONAL_CREATURE_FIRST_STRIKE_TARGET
            and thaw_value(FrozenMap(keywords)) == _OPTIONAL_CREATURE_FIRST_STRIKE_RESULT):
        expected_counter = {**_OPTIONAL_CREATURE_VEHICLE_COUNTERS, "amount": 1}
    else:
        return ()
    if dict(counter) != expected_counter:
        return ()
    counter_required = quantified_counter_target_capabilities(
        effects=(counter,), target_schema=target_schema, mechanic_ids=mechanics,
    )
    keyword_required = target_characteristic_set_capabilities(
        effects=(keywords,), target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not counter_required or not keyword_required:
        return ()
    trigger_required = ("counter.producer.fixed_event_trigger",) if "fixed-counter-event-trigger" in mechanics else ()
    return tuple(sorted({TARGET_SET_SEQUENCE_CAPABILITY, *counter_required, *keyword_required, *trigger_required}))


def target_set_result_sequence_capabilities(*,effects,target_schema,mechanic_ids):
    if effects and effects[0].get("op") == "place_counters_on_targets":
        return _optional_counter_keyword_sequence_capabilities(effects, target_schema, mechanic_ids)
    if (not 2<=len(effects)<=4 or not isinstance(target_schema,Mapping)
            or 'count_parameter' in target_schema or effects[0].get('op') not in {'tap_targets','untap_targets',CHARACTERISTICS}):return ()
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from .counter_target_set_shapes import quantified_counter_target_capabilities
    from .target_characteristic_sets import target_characteristic_set_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities,fixed_scry_node_capabilities
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities
    mechanics=set(mechanic_ids)-{TARGET_SET_SEQUENCE_MECHANIC}
    required={TARGET_SET_SEQUENCE_CAPABILITY};targeted=0;independent=False
    next_untap = effects[0].get(TAP_TARGET_SET_UNTAP_FIELD) is True
    characteristic_prefix=effects[0].get('op')==CHARACTERISTICS
    for effect in effects:
        uses_set=effect.get('cards')=='$targets'
        if uses_set:
            if independent or effect.get('op') not in _TARGET_OPERATIONS:return ()
            targeted+=1
        else:
            if characteristic_prefix:
                if effect.get('op') not in CHARACTERISTIC_TAIL_OPERATIONS:return ()
                owned=_characteristic_tail_capabilities(effect,mechanics)
                if not owned:return ()
                independent=True;required.update(owned)
                continue
            if effect.get('op') not in {'draw','scry','create_token'}:return ()
            if effect.get('op')=='create_token' and not next_untap:
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
        leaf_mechanics = mechanics
        if operation == 'create_token' and next_untap:
            leaf_mechanics = mechanics - {'fixed-homogeneous-target-set', 'cr-115-targets', 'tap-and-untap'}
        owned=resolver(effects=(effect,),target_schema=target_schema if uses_set else None,mechanic_ids=leaf_mechanics)
        if not owned:return ()
        required.update(owned)
    return tuple(sorted(required)) if targeted>=2 or next_untap or characteristic_prefix and targeted==1 and independent else ()


def is_closed_target_set_result_sequence_program(program):
    required=target_set_result_sequence_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def target_set_result_sequence_runtime_dependencies(program):
    effects=program.effects
    target_leaves=[e for e in effects if isinstance(e,Mapping) and (e.get('op') in _TARGET_OPERATIONS-{CHARACTERISTICS}
        or e.get('op')==CHARACTERISTICS and (e.get('schema_version')==6 or 'maximum_targets' in e))]
    characteristic_tail=(len(effects)>1 and effects[0].get('op')==CHARACTERISTICS
        and effects[0].get('schema_version')==6
        and set(effects[0])=={'op','schema_version','cards','maximum_targets','power','toughness','keywords'}
        and type(effects[0].get('power'))is int and type(effects[0].get('toughness'))is int
        and all(effect.get('op') in CHARACTERISTIC_TAIL_OPERATIONS for effect in effects[1:]))
    shared_set = len(target_leaves) >= 2 or characteristic_tail or (
        len(effects)>1 and effects[0].get(TAP_TARGET_SET_UNTAP_FIELD) is True)
    if not shared_set and TARGET_SET_SEQUENCE_MECHANIC not in program.coverage:return ()
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies
    scoped=composed_spell_family_runtime_dependencies(program,capability=TARGET_SET_SEQUENCE_CAPABILITY,
        mechanic=TARGET_SET_SEQUENCE_MECHANIC)
    if scoped is not None:return scoped
    if TARGET_SET_SEQUENCE_MECHANIC in program.coverage:
        from .capabilities import capability_dependencies_for_node
        required=capability_dependencies_for_node(effects=effects,target_schema=program.target_schema,
            mechanic_ids=set(program.coverage)-{'generated_oracle_ir','spell_resolution'},
            cost_schema=getattr(program,'cost_schema',None))
        if TARGET_SET_SEQUENCE_CAPABILITY in required:return required
    if not shared_set:
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
        if effect.get('op')=='create_token':
            if effects[0].get('op')==CHARACTERISTICS:
                from .token_creation_capability_shapes import _fixed_token_effect_closure
                closed=_fixed_token_effect_closure(effect)
                if closed is not None:mechanics.update(closed[1])
            else:mechanics.update(('cr-111-tokens','fixed-token-production','investigate'))
        if effect.get('op')=='scry':mechanics.add('scry')
        if effect.get('op')=='draw':mechanics.add('cr-121-drawing-a-card')
        if effect.get('op') in {'life','lose_life'}:mechanics.add('cr-119-life')
        if effect.get('op')=='amass':mechanics.update(('amass','cr-111-tokens','cr-122-counters'))
    required=target_set_result_sequence_capabilities(effects=effects,target_schema=program.target_schema,mechanic_ids=mechanics)
    if not required:raise ValueError('Same-set results lack their closed shared schema and primitive owners')
    return required
