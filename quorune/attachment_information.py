from __future__ import annotations

"""Refresh explicitly versioned pending attachment information at departure."""

from .attachment_references import (
    ATTACHMENT_INFORMATION_VERSION, SourceAttachmentSnapshot,
    capture_source_attachment_snapshot,
)


def _departure_context(cards, context, departing):
    version=context.get(ATTACHMENT_INFORMATION_VERSION)
    if version is None:return None
    if type(version) is not int or version!=1:
        raise ValueError('Attachment source information version is unsupported')
    snapshot=SourceAttachmentSnapshot.from_dict(context.get('source_attachment_snapshot'))
    source=departing.get(snapshot.source.object_id)
    if source is None or source.zone!='battlefield' or source.logical_object_id!=snapshot.source.logical_object_id:
        return None
    if context.get('source_logical_object_id')!=snapshot.source.logical_object_id:
        raise ValueError('Attachment source information is not bound to its source incarnation')
    current=capture_source_attachment_snapshot(cards,source,snapshot.relation)
    return {**context,'source_attachment_snapshot':current.to_dict()}


def pin_attachment_information_departures(host, cards, *, error_type=ValueError):
    """Prepare every update before committing through canonical zone ownership."""
    try:
        departing={c.object_id:c for c in cards};updates=[];pending=[]
        for item in host.state.stack:
            context=_departure_context(host.state.cards,item.context,departing)
            if context is not None:updates.append((item,context))
        from .trigger_batches import PendingTriggerBatch
        from .trigger_processing import replace_pending_trigger_batch
        for index,batch in enumerate(getattr(host.state,'pending_trigger_batches',())):
            data=batch.to_dict();changed=False
            for group in data['groups']:
                for frame in group['items']:
                    context=_departure_context(host.state.cards,frame['context'],departing)
                    if context is not None:frame['context']=context;changed=True
            if changed:pending.append((index,PendingTriggerBatch.from_dict(data)))
        for item,context in updates:item.context=context
        for index,batch in pending:replace_pending_trigger_batch(host,index,batch)
    except (TypeError,ValueError,KeyError) as exc:
        raise error_type(str(exc)) from exc
