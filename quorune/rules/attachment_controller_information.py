from __future__ import annotations

"""Current or last-known controller of a source's attached object, without targets."""

from collections.abc import Mapping
from dataclasses import dataclass
from ..attachment_references import (
    AttachmentObjectIdentity,AttachmentReferenceKind,AttachmentReferenceSpec,
    SourceAttachmentSnapshot,capture_source_attachment_snapshot,ATTACHMENT_INFORMATION_VERSION,
)

ATTACHMENT_CONTROLLER_KIND='source_attachment_controller_information'
ATTACHMENT_CONTROLLER_CAPABILITY='object.reference.source_attachment_controller'
CONTROLLER_VERSION='source_attachment_controller_information_version'
CONTROLLER_CONTEXT='source_attachment_controller_information'


def attachment_controller_reference(reference):
    if reference!=AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED,'artifact',schema_version=2):
        raise ValueError('Attached controller information requires its closed artifact reference')
    return {'kind':ATTACHMENT_CONTROLLER_KIND,'schema_version':1,'reference':reference.to_dict()}


def attachment_controller_spec(value):
    if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','reference'}
            or value.get('kind')!=ATTACHMENT_CONTROLLER_KIND
            or type(value.get('schema_version')) is not int or value['schema_version']!=1):
        raise ValueError('Attached controller reference uses a closed version-one schema')
    spec=AttachmentReferenceSpec.from_dict(value['reference'])
    attachment_controller_reference(spec)
    return spec


def uses_attachment_controller(value):
    if isinstance(value,Mapping):
        if value.get('kind')==ATTACHMENT_CONTROLLER_KIND:
            attachment_controller_spec(value);return True
        return any(uses_attachment_controller(child) for child in value.values())
    if isinstance(value,(tuple,list)):return any(uses_attachment_controller(child) for child in value)
    return False


@dataclass(frozen=True,slots=True)
class AttachedControllerInformation:
    source:AttachmentObjectIdentity
    attached_object:AttachmentObjectIdentity|None
    controller:str|None

    def __post_init__(self):
        if not isinstance(self.source,AttachmentObjectIdentity):raise ValueError('Controller information lacks its source identity')
        if self.attached_object is None:
            if self.controller is not None:raise ValueError('An unattached source has no attached controller')
        elif (not isinstance(self.attached_object,AttachmentObjectIdentity)
                or type(self.controller) is not str or not self.controller or self.controller.startswith('$')):
            raise ValueError('Attached controller information requires an exact object and controller')

    def to_dict(self):
        return {'schema_version':1,'source':self.source.to_dict(),
            'attached_object':self.attached_object.to_dict() if self.attached_object else None,'controller':self.controller}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','source','attached_object','controller'}
                or type(value.get('schema_version')) is not int or value['schema_version']!=1):
            raise ValueError('Attached controller information uses a closed version-one schema')
        return cls(AttachmentObjectIdentity.from_dict(value['source']),
            AttachmentObjectIdentity.from_dict(value['attached_object']) if value['attached_object'] is not None else None,
            value['controller'])


def _capture(cards,snapshot):
    target=cards.get(snapshot.attached_object.object_id) if snapshot.attached_object else None
    if target is not None and (target.logical_object_id,target.ref)!=(
            snapshot.attached_object.logical_object_id,snapshot.attached_object.reference):
        raise ValueError('Captured attachment controller names a different incarnation')
    return AttachedControllerInformation(snapshot.source,snapshot.attached_object,target.controller if target else None)


def capture_attachment_controller_context(cards,program,snapshot):
    if not uses_attachment_controller(program.effects):return {}
    return {CONTROLLER_VERSION:1,CONTROLLER_CONTEXT:_capture(cards,snapshot).to_dict()}


def _context(context):
    if type(context.get(ATTACHMENT_INFORMATION_VERSION)) is not int or context[ATTACHMENT_INFORMATION_VERSION]!=1:
        raise ValueError('Attached controller information requires captured attachment departure information')
    if type(context.get(CONTROLLER_VERSION)) is not int or context[CONTROLLER_VERSION]!=1:
        raise ValueError('Attached controller information version is unsupported')
    snapshot=SourceAttachmentSnapshot.from_dict(context.get('source_attachment_snapshot'))
    info=AttachedControllerInformation.from_dict(context.get(CONTROLLER_CONTEXT))
    if info.source!=snapshot.source or context.get('source_logical_object_id')!=snapshot.source.logical_object_id:
        raise ValueError('Attached controller information is not bound to its original source')
    return snapshot,info


def attachment_controller_departure_context(cards,context,departing):
    """Prepare immutable updates for the existing canonical departure owner."""
    snapshot,info=_context(context)
    source=cards.get(snapshot.source.object_id)
    current=(source is not None and source.zone=='battlefield'
        and source.logical_object_id==snapshot.source.logical_object_id)
    if current:
        candidate=capture_source_attachment_snapshot(cards,source,snapshot.relation)
        target=candidate.attached_object
        changed=(source.object_id in departing or target is not None and target.object_id in departing
            and departing[target.object_id].logical_object_id==target.logical_object_id)
        if not changed:return None
        snapshot=candidate;info=_capture(cards,snapshot)
    else:
        target=snapshot.attached_object
        card=departing.get(target.object_id) if target else None
        if card is None or card.logical_object_id!=target.logical_object_id:return None
        info=AttachedControllerInformation(snapshot.source,target,card.controller)
    return {**context,'source_attachment_snapshot':snapshot.to_dict(),CONTROLLER_CONTEXT:info.to_dict()}


def resolve_attachment_controller(host,value,item):
    spec=attachment_controller_spec(value);snapshot,info=_context(item.context)
    if (snapshot.relation is not spec.relation or item.source_object_id!=snapshot.source.object_id):
        raise ValueError('Attached controller reference and stack source disagree')
    source=host.state.cards.get(snapshot.source.object_id)
    if source is not None and source.logical_object_id==snapshot.source.logical_object_id:
        if source.zone!='battlefield' or source.phased_out:return None
        snapshot=capture_source_attachment_snapshot(host.state.cards,source,snapshot.relation)
    target=snapshot.attached_object
    if target is None:return None
    card=host.state.cards.get(target.object_id)
    if (card is not None and card.logical_object_id==target.logical_object_id
            and card.ref==target.reference and card.zone=='battlefield' and not card.phased_out):
        return host._effective_card_data(card).get('controller',card.controller)
    if info.attached_object!=target:
        raise ValueError('Attached controller departure information names a different object')
    return info.controller
