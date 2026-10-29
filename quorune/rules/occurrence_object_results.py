from __future__ import annotations

"""Runtime minimum and event compatibility for sealed occurrence results."""

from collections.abc import Mapping


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
    if (program.event not in {'permanent.tap','permanent.untap'} or program.target_schema is not None
            or len(program.effects)!=1 or program.effects[0]!={'op':'destroy','card':'$event.permanent_object'}):
        raise ValueError('Occurrence destruction requires one untargeted tap-state permanent incarnation')
    return ('permanent.destroy.effect','resolution.event_object.fixed_result')


def occurrence_object_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0];mechanics=set(mechanic_ids)
    expected={'op':'place_player_counters','subjects':'target','counter':'poison','amount':1,'source':'$source',
        'target':{'kind':'occurrence_controller_information','schema_version':1}}
    if effect==expected and 'cr-122-counters' in mechanics and type(effect.get('amount')) is int and type(effect['target'].get('schema_version')) is int:
        return ('resolution.event_object.fixed_result','counter.producer.fixed_player_effect','resolution.target_controller_information')
    return ()
