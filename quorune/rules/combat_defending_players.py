"""Infer and validate defending-player use independently of result annotations."""
from collections.abc import Mapping
from .event_player_result_shapes import _event_player_reference_is_closed


def combat_defending_player_runtime_dependencies(program):
    if program.event not in {'creature.becomes_blocked','creature.unblocked'}:return ()
    def uses(value):
        if isinstance(value,Mapping):return any(uses(child) for child in value.values())
        if isinstance(value,(list,tuple)):return any(uses(child) for child in value)
        return value=='$context.defending_player'
    if not uses(program.effects):return ()
    if program.active_zone!='battlefield' or not _event_player_reference_is_closed(program,'$context.defending_player'):
        raise ValueError('Combat defending-player result lacks its closed attacker occurrence scope')
    if {'afflict','fixed-keyword-event-effect'} <= set(program.coverage):
        # Afflict already owns this same life-loss instruction. Reuse its
        # complete typed shape rather than requiring the prose-result marker.
        from ..compiler.keyword_event_effect_nodes import FixedKeywordEventEffectSpec
        if len(program.effects)==1:
            spec=FixedKeywordEventEffectSpec('afflict',program.effects[0].get('amount'))
            if (program.event==spec.event and program.event_condition==spec.event_condition
                    and program.target_schema==spec.target_schema and tuple(program.effects)==spec.effects):
                return (spec.capability_id,)
    required=set()
    for effect in program.effects:
        op=effect.get('op')
        if op in {'life','lose_life','draw','damage'}:
            from .event_player_result_shapes import fixed_event_player_result_node_capabilities
            owned=fixed_event_player_result_node_capabilities(effects=(effect,),target_schema=program.target_schema,mechanic_ids=program.coverage)
        elif op=='mill':
            from .mill_capability_shapes import fixed_mill_node_capabilities
            owned=fixed_mill_node_capabilities(effects=(effect,),target_schema=program.target_schema,mechanic_ids=program.coverage)
        elif op=='choose_cards_apnap':
            from .affected_player_discard_capability_shapes import fixed_context_player_discard_node_capabilities
            owned=fixed_context_player_discard_node_capabilities(effects=(effect,),target_schema=program.target_schema,mechanic_ids=program.coverage)
            if not owned:
                from .event_player_choice_shapes import event_player_sacrifice_node_capabilities
                owned=event_player_sacrifice_node_capabilities(effects=(effect,),target_schema=program.target_schema,mechanic_ids=program.coverage)
        elif op=='offer_optional_mana_payment':
            from .fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
            owned=fixed_payment_event_player_runtime_dependencies(program)
        else:owned=()
        if not owned:raise ValueError('Combat defending-player result lacks an independently closed primitive')
        required.update(owned)
    return tuple(sorted(required))
