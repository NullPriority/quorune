from __future__ import annotations

"""Validate and expand Devour plans for the canonical simultaneous zone owner."""

from .devour import DevourProposal,DevourQuality
from .errors import GameRuleError
from .zone_trigger_events import ZoneTransitionKind


def expand_devour_zone_plans(host,changes,prepared,kinds):
    if not any(prepared[oid].devour_sacrifices for oid,_ in changes):return tuple(changes),prepared,kinds
    expanded=list(changes);plans=dict(prepared);transitions=dict(kinds);reserved={oid for oid,_ in changes}
    for oid,_ in changes:
        parent=prepared[oid];linked={p.object_id:p for p in parent.devour_sacrifices}
        for raw in parent.event.payload.get('devour_proposals',()):
            proposal=DevourProposal.from_dict(raw)
            for selected in proposal.objects:
                card=host.state.cards.get(selected.object_id)
                if (card is None or card.zone!='battlefield' or card.phased_out or card.controller!=proposal.controller
                        or card.logical_object_id!=selected.logical_object_id or card.ref!=selected.ref or card.object_id in reserved):
                    raise GameRuleError('A Devour sacrifice changed after its captured choice')
                data=host._effective_card_data(card);types,subtypes,_=host._type_parts(str(data.get('type_line') or ''))
                quality=proposal.spec.quality
                if (quality is DevourQuality.CREATURE and 'creature' not in types
                        or quality is DevourQuality.ARTIFACT and 'artifact' not in types
                        or quality is DevourQuality.FOOD and not ('artifact' in types and 'food' in subtypes)):
                    raise GameRuleError('A Devour sacrifice no longer matches its quality')
                plan=linked[selected.object_id]
                expanded.append((card.object_id,plan.requested_destination));plans[card.object_id]=plan
                transitions[card.object_id]=ZoneTransitionKind.SACRIFICE;reserved.add(card.object_id)
    return tuple(expanded),plans,transitions


def commit_single_devour_entry(owner,plan,*,reason,log,aura_target_ref,semantic_events):
    card=plan.card;prepared=plan.prepared_replacement
    owner.move_cards_simultaneously(((card.object_id,plan.requested_destination),),reason=reason,log=log,
        destination_controllers={card.object_id:prepared.destination_controller} if plan.requested_destination=='battlefield' else {},
        aura_target_refs={card.object_id:aura_target_ref} if aura_target_ref is not None else {},
        transition_kinds={card.object_id:prepared.transition_kind},prepared_replacements={card.object_id:prepared},
        entry_faces={card.object_id:plan.enter_face} if plan.enter_face is not None else {},
        publish_events=semantic_events)
    return owner.state.cards[card.object_id]


def prepare_token_devour_sacrifices(host,entries,*,reason):
    """Prepare the zone-owned half before the token owner allocates anything."""
    linked=tuple(p for entry in entries.values() for p in entry.devour_sacrifices)
    if not linked:return None
    roots=tuple((oid,entry.requested_destination) for oid,entry in entries.items())
    changes,prepared,kinds=expand_devour_zone_plans(host,roots,dict(entries),{})
    changes=tuple((oid,destination) for oid,destination in changes if oid not in entries)
    prepared={oid:prepared[oid] for oid,_ in changes}
    from .zone_transitions import ZoneTransitionOwner
    owner=ZoneTransitionOwner(host)
    changes,prepared,kinds,sources=owner._prepare_simultaneous_zone_batch(changes,{}, {}, {},tapped=None,
        effect_entry_counters=None,replacement_selections=(),kinds=kinds,prepared_replacements=prepared,log=False)
    return owner,changes,prepared,kinds,sources


def commit_token_devour_sacrifices(prepared,*,timestamp,reason):
    if prepared is None:return None
    owner,changes,plans,kinds,sources=prepared
    snapshots=owner._commit_simultaneous_zone_batch(changes,plans,kinds,{}, {}, {},sources,
        reason=reason,log=False,destination_timestamp=timestamp)
    return owner,snapshots,plans,kinds


def publish_token_devour_sacrifices(committed,*,reason):
    if committed is None:return
    owner,snapshots,plans,kinds=committed
    owner._publish_simultaneous_zone_batch(snapshots,plans,kinds,reason=reason)


def validate_devour_resume(host,batch,effects,selection,*,error_type):
    """A saved choice keeps captured identities instead of refreshing authority."""
    from .replacement.operations import DevourEntryOperation
    selected=[]
    if isinstance(selection,dict) and selection.get('objects'):
        effect=next((e for e in effects if e.effect_id==selection['effect_id']),None)
        operations=tuple(op for op in effect.operations if isinstance(op,DevourEntryOperation)) if effect is not None else ()
        if len(operations)!=1:raise error_type('Selected replacement has no Devour identity contract')
        operation=operations[0];candidates={obj.ref:obj for obj in operation.candidates}
        for ref in selection['objects']:
            if ref not in candidates:raise error_type('Selected Devour object was not captured')
            selected.append((operation.controller,operation.spec,candidates[ref]))
    for event in batch.events:
        for raw in event.payload.get('devour_proposals',()):
            proposal=DevourProposal.from_dict(raw)
            selected.extend((proposal.controller,proposal.spec,obj) for obj in proposal.objects)
    for controller,spec,obj in selected:
        card=host.state.cards.get(obj.object_id)
        if (card is None or card.zone!='battlefield' or card.phased_out or card.controller!=controller
                or card.logical_object_id!=obj.logical_object_id or card.ref!=obj.ref):
            raise error_type('Devour selected incarnation changed before replacement resume')
        data=host._effective_card_data(card);types,subtypes,_=host._type_parts(str(data.get('type_line') or ''))
        if (spec.quality is DevourQuality.CREATURE and 'creature' not in types
                or spec.quality is DevourQuality.ARTIFACT and 'artifact' not in types
                or spec.quality is DevourQuality.FOOD and not ('artifact' in types and 'food' in subtypes)):
            raise error_type('Devour selected quality changed before replacement resume')
