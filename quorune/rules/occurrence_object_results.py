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
