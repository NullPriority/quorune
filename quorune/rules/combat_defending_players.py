"""Infer and validate defending-player use independently of result annotations."""
from collections.abc import Mapping

from ..affected_permanents import DEFENDING_PLAYER_UNTAP_SET_FIELD
from ..event_player_modifier_model import DEFENDING_PLAYER_REFERENCE
from .effect_shape_walkers import nested_effects
from .event_player_result_shapes import _event_player_reference_is_closed
from .hand_inspection import (
    DEFENDING_HAND_OBSERVATION_CAPABILITY,
    DEFENDING_HAND_OBSERVATION_VERSION_FIELD,
    FIXED_HAND_INSPECTION_OPERATION,
)


def _qualified_defender_fixed_result_dependencies(program):
    if program.event!='creature.attacks' or not any(effect.get('op') in {'draw','life','lose_life'}
            and effect.get('player')=='$context.defending_player' for effect in program.effects):return ()
    from .qualified_attacks import qualified_attack_defender_condition_is_closed
    if not qualified_attack_defender_condition_is_closed(program.event,program.event_condition):return ()
    from .event_player_result_shapes import fixed_event_player_result_node_capabilities
    from ..compiler.event_player_results import EVENT_PLAYER_RESULT_MECHANIC
    required=fixed_event_player_result_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=(*program.coverage,EVENT_PLAYER_RESULT_MECHANIC))
    if program.active_zone!='battlefield' or not required:
        raise ValueError('Qualified defending-player result lacks an independently closed primitive')
    return required


def _uses_defending_poison_recipient(value):
    if isinstance(value, Mapping):
        if value.get("op") == "place_player_counters" and value.get("target") == "$context.defending_player":
            return True
        return any(_uses_defending_poison_recipient(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_uses_defending_poison_recipient(child) for child in value)
    return False


def _uses_defending_player_untap(effect):
    if DEFENDING_PLAYER_UNTAP_SET_FIELD in effect:
        return True
    group = effect.get("set")
    return (effect.get("op") == "set_public_tap_state" and isinstance(group, Mapping)
            and group.get("target_controller") == DEFENDING_PLAYER_REFERENCE)


def _defending_creature_untap_dependencies(program):
    from .public_zone_move_capability_shapes import fixed_public_tap_state_set_node_capabilities

    if ((program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
            or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
            or len(program.effects) != 1 or DEFENDING_PLAYER_UNTAP_SET_FIELD not in program.effects[0]):
        raise ValueError("Defending-player creature untap requires one untargeted source attack")
    required = fixed_public_tap_state_set_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Defending-player creature untap requires its complete current public set")
    return tuple(sorted({*required, "trigger.placement.apnap"}))


def _uses_defending_player(value):
    if isinstance(value, Mapping):
        return any(_uses_defending_player(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_uses_defending_player(child) for child in value)
    return value == "$context.defending_player"


def _uses_defending_hand_observation(effect):
    return (DEFENDING_HAND_OBSERVATION_VERSION_FIELD in effect
            or (effect.get("op") == FIXED_HAND_INSPECTION_OPERATION
                and effect.get("target") == DEFENDING_PLAYER_REFERENCE))


def _defending_hand_observation_dependencies(program):
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities

    if ((program.event, program.active_zone) != ("creature.becomes_blocked", "battlefield")
            or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
            or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
            or getattr(program, "handlers", ())):
        raise ValueError("Private defending hand observation requires one exact blocked source")
    required = fixed_optional_effect_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
    )
    if DEFENDING_HAND_OBSERVATION_CAPABILITY not in required:
        raise ValueError("Private defending hand observation requires one exact optional look")
    return (*required, "trigger.event.normalized_public_action", "trigger.placement.apnap")


def combat_defending_player_runtime_dependencies(program):
    from .counter_scaled_annihilator import counter_scaled_annihilator_program_dependencies

    if annihilator := counter_scaled_annihilator_program_dependencies(program):
        return annihilator
    from .defending_creature_damage import defending_creature_damage_runtime_dependencies

    if damage := defending_creature_damage_runtime_dependencies(program):
        return damage
    if any(_uses_defending_hand_observation(effect)
           for effect in nested_effects((program.effects, getattr(program, "handlers", ())))):
        return _defending_hand_observation_dependencies(program)
    from ..random_discard import RANDOM_DISCARD_OPERATION, random_discard_node_capabilities
    from .event_player_choice_shapes import attacked_defender_exile_program_dependencies, is_typed_defender_exile

    if any(
        is_typed_defender_exile(effect)
        for effect in nested_effects((program.effects, getattr(program, "handlers", ())))
    ):
        return attacked_defender_exile_program_dependencies(program)
    if any(effect.get("op") == RANDOM_DISCARD_OPERATION and _uses_defending_player(effect.get("players"))
           for effect in nested_effects((program.effects, getattr(program, "handlers", ())))):
        if (
            (program.event, program.active_zone) != ("creature.becomes_blocked", "battlefield")
            or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
            or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
            or len(program.effects) != 1 or getattr(program, "handlers", ())
        ):
            raise ValueError("Random defending-player discard requires one sealed attacker occurrence")
        owned = random_discard_node_capabilities(
            effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
        )
        if not owned or program.effects[0].get("players") != ["$context.defending_player"]:
            raise ValueError("Random defending-player discard requires its complete fixed result")
        return owned
    if any(_uses_defending_player_untap(effect) for effect in nested_effects(program.effects)):
        return _defending_creature_untap_dependencies(program)
    if _uses_defending_poison_recipient(program.effects):
        from .event_player_result_shapes import unblocked_defender_poison_program_capabilities
        poison = unblocked_defender_poison_program_capabilities(program)
        if not poison:
            raise ValueError("Defending-player poison requires one exact intrinsic unblocked occurrence")
        return poison
    if program.event=='creature.attacks':return _qualified_defender_fixed_result_dependencies(program)
    if program.event not in {'creature.becomes_blocked','creature.unblocked'}:return ()
    if not _uses_defending_player(program.effects):return ()
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
        elif op=='discard_hands_then_draw':
            from .discard_result_shapes import event_player_discard_result_runtime_dependencies
            owned=event_player_discard_result_runtime_dependencies(program)
        elif op=='offer_optional_mana_payment':
            from .fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
            owned=fixed_payment_event_player_runtime_dependencies(program)
        else:owned=()
        if not owned:raise ValueError('Combat defending-player result lacks an independently closed primitive')
        required.update(owned)
    return tuple(sorted(required))
