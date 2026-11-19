from __future__ import annotations

"""Payload authority for fixed damage to a non-targeted referenced recipient."""

from collections.abc import Mapping
from ..attachment_references import AttachmentReferenceSpec,AttachmentReferenceKind,ATTACHMENT_INFORMATION_CAPABILITY
from ..event_counter_reference import EVENT_ENTRY_OBJECT
from ..compiler.referenced_damage_templates import REFERENCED_DAMAGE_CAPABILITY


def referenced_damage_kind(value):
    if value in (EVENT_ENTRY_OBJECT,'$event.permanent_object'):return 'event'
    if isinstance(value,Mapping) and value.get('kind')=='source_attachment':
        spec=AttachmentReferenceSpec.from_dict(value)
        if spec!=AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED,'permanent',schema_version=2):
            raise ValueError('Referenced attachment damage requires its closed enchanted-object reference')
        return 'attachment'
    if isinstance(value,Mapping) and value.get('kind')=='source_attachment_controller_information':
        from .attachment_controller_information import attachment_controller_spec
        attachment_controller_spec(value)
        return 'controller'
    return None


def referenced_damage_dependencies(effect):
    if not isinstance(effect,Mapping) or effect.get('op')!='damage':return ()
    kind=referenced_damage_kind(effect.get('target'))
    if kind is None:return ()
    if (set(effect)!={'op','source','target','amount'} or effect['source']!='$source'
            or type(effect['amount']) is not int or effect['amount']<1):
        raise ValueError('Referenced damage requires one closed positive source-permanent instruction')
    caps={REFERENCED_DAMAGE_CAPABILITY,'damage.amount.positive','damage.source.stack_current_or_lki',
        'damage.result.player_life' if kind=='controller' else 'damage.result.multitype_permanent'}
    if kind in {'attachment','controller'}:caps.update(('attachment.reference.current_or_lki',ATTACHMENT_INFORMATION_CAPABILITY))
    if kind=='controller':
        from .attachment_controller_information import ATTACHMENT_CONTROLLER_CAPABILITY
        caps.add(ATTACHMENT_CONTROLLER_CAPABILITY)
    return tuple(sorted(caps))


def referenced_damage_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or target_schema is not None or 'cr-120-damage' not in mechanic_ids:return ()
    try:return referenced_damage_dependencies(effects[0])
    except (TypeError,ValueError):return ()


def referenced_damage_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            dependencies=referenced_damage_dependencies(value)
            if dependencies:
                if program.active_zone!='battlefield':raise ValueError('Referenced damage requires its battlefield source')
                if value['target']==EVENT_ENTRY_OBJECT and program.event not in {'creature.enter','permanent.enter'}:
                    raise ValueError('Entry damage recipient requires a creature-entry occurrence')
                if value['target']=='$event.permanent_object' and program.event!='creature.attacks':
                    raise ValueError('Attack damage recipient requires its attack occurrence')
                required.update(dependencies)
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def is_closed_referenced_damage_program(program):
    required=referenced_damage_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=('cr-120-damage',))
    if not required or not set(required).issubset(program.capability_dependencies):return False
    try:return bool(referenced_damage_runtime_dependencies(program))
    except (TypeError,ValueError):return False
