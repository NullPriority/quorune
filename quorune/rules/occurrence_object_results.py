from __future__ import annotations

"""Runtime minimum and event compatibility for sealed occurrence results."""

from collections.abc import Mapping
from ..block_characteristic_model import BLOCK_CHARACTERISTIC_CONDITION
from ..phasing_model import (PHASE_OUT_CAPABILITY, PHASE_OUT_OPERATION,
                            PHASING_CAPABILITY, validate_phase_out_effect)
from .event_subscriptions import FixedEventSubscriptionSet
from .tap_state_capability_shapes import event_next_untap_node_capabilities


def _permanent_receiving_condition(value):
    if not isinstance(value,Mapping):return False
    if set(value)=={'field','op','value'}:
        return value=={'field':'target_kind','op':'eq','value':'permanent'}
    if set(value)=={'all'} and isinstance(value['all'],(list,tuple)):
        return any(_permanent_receiving_condition(child) for child in value['all'])
    if set(value)=={'any'} and isinstance(value['any'],(list,tuple)) and value['any']:
        return all(_permanent_receiving_condition(child) for child in value['any'])
    return False


def _permanent_occurrence_subscriptions(program):
    condition=getattr(program,'event_condition',None)
    subscriptions=FixedEventSubscriptionSet.from_condition(condition)
    values=tuple((child.event,child.condition) for child in subscriptions.subscriptions) if subscriptions else ((program.event,condition),)
    for event,condition in values:
        event=event.removesuffix('.self')
        if event in {'permanent.tap','permanent.untap'}:continue
        if event not in {'damage.received','combat.damage.received'} or not _permanent_receiving_condition(condition):return False
    return True


_PHASE_OUT_CREATURE = {"zones": ["battlefield"], "categories": ["permanent"],
                       "count": 1, "types_any": ["creature"]}


def phase_out_node_capabilities(effects, target_schema):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        subject = validate_phase_out_effect(effect)
    except ValueError:
        return ()
    required = (PHASE_OUT_CAPABILITY, PHASING_CAPABILITY)
    if subject == "source":
        return required if target_schema is None else ()
    if (effect["card"] != "$target.0" or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int):
        return ()
    from ..replacement.immutable import FrozenMap
    allowed = (_PHASE_OUT_CREATURE, {**_PHASE_OUT_CREATURE, "controller_relation": "you"},
               {**_PHASE_OUT_CREATURE, "types_any": ["artifact", "creature", "land"]})
    if not any(FrozenMap(target_schema) == FrozenMap(schema) for schema in allowed):
        return ()
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities
    return (*required, "target.revalidate_resolution", *direct_target_predicate_capabilities(target_schema))


def phase_out_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effect_operations
    if PHASE_OUT_OPERATION not in nested_effect_operations(program.effects):
        return ()
    required = phase_out_node_capabilities(program.effects, program.target_schema)
    if (not required or program.active_zone not in ("battlefield", "stack")
            or (program.effects[0]["subject"] == "source" and program.active_zone != "battlefield")):
        raise ValueError("Phase out requires one complete battlefield-source or single-target result")
    return required


def is_closed_phase_out_program(program):
    if len(program.effects) != 1 or program.effects[0].get("op") != PHASE_OUT_OPERATION:
        return False
    try:
        required = phase_out_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def occurrence_object_runtime_dependencies(program):
    from .attachment_action_capability_shapes import targeted_equipment_attachment_runtime_dependencies
    from .attachment_actions import entry_equipment_attachment_runtime_dependencies
    from ..event_card_return import event_library_move_runtime_dependencies

    if required := (entry_equipment_attachment_runtime_dependencies(program)
                    or targeted_equipment_attachment_runtime_dependencies(program)
                    or event_library_move_runtime_dependencies(program)):
        return required
    from .creature_power_damage_capability_shapes import occurrence_power_damage_runtime_dependencies
    if required := occurrence_power_damage_runtime_dependencies(program):
        return required
    if required := phase_out_runtime_dependencies(program):
        return required
    from .combat_removal_shapes import combat_removal_runtime_dependencies
    combat_removal = combat_removal_runtime_dependencies(program)
    if combat_removal:
        return combat_removal
    from .damage_permanent_recipients import damage_permanent_runtime_dependencies
    damaged=damage_permanent_runtime_dependencies(program)
    if damaged:return damaged
    def counterpart(value):
        if isinstance(value, Mapping):return any(counterpart(child) for child in value.values())
        if isinstance(value, (list, tuple)):return any(counterpart(child) for child in value)
        return value == '$event.block_counterpart'
    if counterpart(program.effects):
        from .node_capability_shapes import fixed_source_characteristics_node_capabilities
        from .damage_capability_shapes import fixed_damage_node_capabilities
        from ..delayed_object_model import delayed_object_node_capabilities
        subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
        pairs=tuple((child.event,child.condition) for child in subscriptions.subscriptions) if subscriptions else ((program.event,program.event_condition),)
        def guarantees(condition, field, operation, value):
            if not isinstance(condition, Mapping):return False
            if condition.get('field') == field and condition.get('op') == operation and condition.get('value') == value:
                return True
            if isinstance(condition.get('all'), (list, tuple)):
                return any(guarantees(child, field, operation, value) for child in condition['all'])
            return False
        if (program.event not in {'creature.blocks','creature.becomes_blocked_by'} or program.active_zone != 'battlefield'
                or program.target_schema is not None or not pairs
                or {event for event,_condition in pairs} not in ({'creature.blocks'},{'creature.becomes_blocked_by'},{'creature.blocks','creature.becomes_blocked_by'})
                or any(not guarantees(condition,'card','eq','$source.ref') for _event,condition in pairs)
                or any(event=='creature.blocks' and not guarantees(condition,'blocked_attacker','truthy',True) for event,condition in pairs)):
            raise ValueError('Block counterpart requires the closed source-specific per-creature subscriptions')
        required = (fixed_source_characteristics_node_capabilities(effects=program.effects,
            target_schema=None, mechanic_ids=program.coverage)
            or fixed_damage_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=program.coverage)
            or delayed_object_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=program.coverage))
        if not required:
            required=block_counterpart_zone_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if not required:
            required = event_next_untap_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=program.coverage)
            if required:
                from .block_characteristics import qualified_block_runtime_dependencies
                if any(not guarantees(condition, BLOCK_CHARACTERISTIC_CONDITION, 'eq', True) for _event, condition in pairs):
                    raise ValueError('Next-untap block counterparts require sealed counterpart characteristics')
                required = (*required, *qualified_block_runtime_dependencies(program))
        if not required:
            raise ValueError('Block counterpart requires one independently closed untargeted result')
        return tuple(sorted(set(required) | {'trigger.event.block_pair_counterpart','trigger.event.normalized_public_action'}
            | ({'trigger.subscription.fixed_multi_event'} if subscriptions else set())))
    attached=any(e.get('op')=='destroy' and isinstance(e.get('card'),Mapping) and e['card'].get('kind')=='source_attachment' for e in program.effects)
    if attached:
        from ..attachment_references import AttachmentReferenceSpec
        if program.target_schema is not None or len(program.effects)!=1 or set(program.effects[0])!={'op','card'}:
            raise ValueError('Attached destruction requires one closed untargeted relation')
        AttachmentReferenceSpec.from_dict(program.effects[0]['card'])
        return ('permanent.destroy.effect','attachment.reference.current_or_lki')
    def contains(value):
        if isinstance(value,Mapping):return any(contains(v) for v in value.values())
        if isinstance(value,(list,tuple)):return any(contains(v) for v in value)
        return value=='$event.permanent_object'
    controller=any(isinstance(e.get(field),Mapping) and e[field].get('kind')=='occurrence_controller_information'
        for e in program.effects for field in ('target','player'))
    if controller:
        if program.event == 'creature.attacks':
            from .event_player_result_shapes import attack_occurrence_controller_runtime_dependencies
            required = attack_occurrence_controller_runtime_dependencies(program)
            if not required:
                raise ValueError('Attack controller information requires a closed life instruction')
            return required
        if program.event == 'creature.blocks':
            from .event_player_result_shapes import blocking_controller_runtime_dependencies
            required = blocking_controller_runtime_dependencies(program)
            if not required:
                raise ValueError('Blocking controller information requires one closed untargeted result')
            return required
        from ..received_damage_model import RECEIVED_DAMAGE_EVENTS
        from ..target_controller_information import OCCURRENCE_CONTROLLER_ENTRY_EVENTS
        if program.event in OCCURRENCE_CONTROLLER_ENTRY_EVENTS | RECEIVED_DAMAGE_EVENTS:
            from .event_player_result_shapes import event_player_damage_runtime_dependencies
            required = event_player_damage_runtime_dependencies(program) if len(program.effects) == 1 else ()
            if not required:
                raise ValueError('Occurrence controller information requires one closed untargeted damage result')
            return required
        if len(program.effects)==1 and program.effects[0].get('op') in {'life','lose_life','mill','damage'}:
            from .tap_state_event_capability_shapes import tap_state_event_player_node_capabilities
            required=tap_state_event_player_node_capabilities(effects=program.effects,target_schema=program.target_schema,
                mechanic_ids=program.coverage)
        else:required=occurrence_object_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=('cr-122-counters',))
        if program.event not in {'permanent.tap','permanent.untap'} or not required:
            raise ValueError('Occurrence controller result requires a closed untargeted tap-state instruction')
        return required
    if not contains(program.effects):return ()
    if program.event=='creature.attacks':
        from .referenced_damage_shapes import referenced_damage_node_capabilities,referenced_damage_runtime_dependencies
        if referenced_damage_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=('cr-120-damage',)):
            return referenced_damage_runtime_dependencies(program)
        from .attack_event_results import attack_event_characteristic_capabilities
        required=attack_event_characteristic_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if not required:raise ValueError('Attack occurrence characteristics require one closed untargeted result')
        return required
    prohibited=len(program.effects)==1 and program.effects[0].get('regeneration_prohibited') is True
    expected={'op':'destroy','card':'$event.permanent_object',**({'regeneration_prohibited':True} if prohibited else {})}
    if (not _permanent_occurrence_subscriptions(program) or program.target_schema is not None
            or len(program.effects)!=1 or program.effects[0]!=expected):
        raise ValueError('Occurrence destruction requires one sealed untargeted permanent incarnation')
    return ('permanent.destroy.effect','resolution.event_object.fixed_result',*(('permanent.destroy.regeneration_prohibition',) if prohibited else ()))


def occurrence_object_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0];mechanics=set(mechanic_ids)
    expected={'op':'place_player_counters','subjects':'target','counter':'poison','amount':1,'source':'$source',
        'target':{'kind':'occurrence_controller_information','schema_version':1}}
    if effect==expected and 'cr-122-counters' in mechanics and type(effect.get('amount')) is int and type(effect['target'].get('schema_version')) is int:
        return ('resolution.event_object.fixed_result','counter.producer.fixed_player_effect','resolution.target_controller_information')
    return ()


def block_counterpart_zone_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if effect=={'op':'bounce','card':'$event.block_counterpart'} and 'return-to-owner-hand'in mechanic_ids:
        return ('permanent.return.owner_hand','trigger.event.block_pair_counterpart')
    if 'destroy'not in mechanic_ids:return ()
    for prohibited in (False,True):
        expected={'op':'destroy','card':'$event.block_counterpart',**({'regeneration_prohibited':True} if prohibited else {})}
        if effect==expected:return ('permanent.destroy.effect','trigger.event.block_pair_counterpart',
            *(('permanent.destroy.regeneration_prohibition',) if prohibited else ()))
    return ()
