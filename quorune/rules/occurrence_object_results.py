from __future__ import annotations

"""Runtime minimum and event compatibility for sealed occurrence results."""

from collections.abc import Mapping
from .event_subscriptions import FixedEventSubscriptionSet


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


def occurrence_object_runtime_dependencies(program):
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
