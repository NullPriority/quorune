from __future__ import annotations

"""Pure Devour selection proposals beside the existing replacement application."""

from ..devour import DevourProposal
from .model import ReplacementEffectError
from dataclasses import replace
from .immutable import thaw_value


def combine_devour_entry_counters(event,payload,children):
    """One entry puts each same-player, same-kind counter contribution together."""
    if event.kind!='zone.change' or not payload.get('devour_proposals'):return
    grouped={};result=[]
    for child in children:
        value=child.payload
        if (child.kind!='counter.place' or child.affected_object!=event.affected_object
                or value.get('follows_zone_destination') is not True):
            result.append(child);continue
        key=(value.get('counter_name'),value.get('placing_player'))
        if key not in grouped:grouped[key]=len(result);result.append(child);continue
        index=grouped[key];first=result[index]
        if first.applied_effects or child.applied_effects or first.children or child.children:
            raise ReplacementEffectError('Entry counter contributions must combine before their replacements')
        combined=thaw_value(first.payload)
        for field in ('amount','requested_amount'):combined[field]+=value[field]
        result[index]=replace(first,payload=combined)
    children[:]=result


def apply_devour_entry_operation(event,payload,children,scope,operation,*,effect_id,objects):
    """Resolve immutable selected identities without moving or sacrificing cards."""
    if (event.kind!='zone.change' or payload.get('destination')!='battlefield' or scope is None
            or payload.get('destination_controller')!=operation.controller or event.affected_object is None):
        raise ReplacementEffectError('Devour selection belongs to its controlled battlefield entry')
    values=() if objects is None else objects
    if (not isinstance(values,(tuple,list)) or any(type(v) is not str or not v for v in values)
            or len(set(values))!=len(values)):
        raise ReplacementEffectError('Devour requires distinct selected references')
    eligible_ids=set(scope.eligible_zone_choices(c.object_id for c in operation.candidates))
    candidates={c.ref:c for c in operation.candidates if c.object_id in eligible_ids}
    if any(ref not in candidates for ref in values):raise ReplacementEffectError('Devour selected an entering, reserved or unadvertised object')
    proposal=DevourProposal(operation.controller,operation.component_id,operation.spec,tuple(candidates[ref] for ref in values))
    prior=list(payload.get('devour_proposals',()))
    if any(v['component_id']==proposal.component_id for v in prior):raise ReplacementEffectError('A Devour component cannot apply twice to one entry')
    payload['devour_proposals']=[*prior,proposal.to_dict()]
    reserved=scope.reserve_zone_changes(o.object_id for o in proposal.objects)
    if proposal.counter_amount:
        from .operations import CreateAffectedObjectCounter
        from .application import _create_affected_object_counter_event
        _create_affected_object_counter_event(event,payload,children,CreateAffectedObjectCounter(counter_name='+1/+1',amount=proposal.counter_amount,
            placing_player=operation.controller,source_ref=str(payload.get('object_ref') or ''),sequence=len(prior)),effect_id=effect_id)
    return reserved
