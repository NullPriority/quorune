from __future__ import annotations

"""Physical split coordination through the existing zone and object owners."""

from dataclasses import replace
from .attachments import clear_object_attachment_relations
from .composition_departure import CompositionZoneFact,CompositionZoneRole
from .errors import GameRuleError,StateInvariantError
from .game_object_store import GameObjectStore
from .model import CardInstance
from .replacement.immutable import thaw_value
from .semantic_runtime import log_applied_zone_replacements
from .trigger_processing import enqueue_trigger_batch
from .zone_transition_model import ZoneDepartureSnapshot
from .zone_trigger_events import ZoneChangeOccurrence,ZoneTransitionKind,validate_zone_transition_request
from .zone_trigger_processing import dispatch_zone_change_occurrence,record_zone_change_history


def _prepare(owner,object_id,destination,order,chooser,actor,reason,position,selections,kind,prepared_replacement=None):
    root=validate_zone_transition_request(owner.state.cards,object_id,destination,kind)
    storage=root.permanent_composition
    if storage is None or len(storage.parts)<2:raise GameRuleError('A split requires a current stored composition')
    storage.validate_carrier(root)
    if (destination=='battlefield' or not isinstance(order,(list,tuple))
            or any(type(value) is not str for value in order)
            or len(order)!=len(set(order)) or set(order)!=set(storage.topology.physical_object_ids)):
        raise GameRuleError('A split requires the exact physical order and a departure destination')
    owner.host._require_seat(actor,in_game=True)
    if chooser is not None:owner.host._require_seat(chooser,in_game=True)
    if storage.topology.commander_designation_ids and destination not in {'graveyard','exile','hand','library'}:
        raise GameRuleError('Composed commander destination choices require their represented group continuation')
    plan=owner._prepare_move(root,destination,controller=None,tapped=None,entry_pay_life=False,enter_face=None,
        battle_protector=None,aura_target_ref=None,resolving_as_aura_spell=False,aura_enchant_spec=None,
        position=position,reason=reason,log=False,replacement_selections=selections,
        prepared_replacement=prepared_replacement,transition_kind=kind)
    if isinstance(plan,CardInstance) or plan.destination=='battlefield':return root,storage,None
    if storage.topology.commander_designation_ids and plan.destination not in {'graveyard','exile','hand','library'}:
        raise GameRuleError('Composed commander destination choices require their represented group continuation')
    from .composition_commander_replacement_model import commander_component_destinations,payload_commander_components
    from .commander_zones import capture_composed_commander_zone_components
    try:
        components=payload_commander_components(plan.prepared_replacement.event.payload)
        if components!=capture_composed_commander_zone_components(root):
            raise ValueError('Commander replacement snapshot does not match the current physical store')
        redirects=commander_component_destinations(plan.prepared_replacement.event.payload)
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if redirects and plan.prepared_replacement.counter_events:
        raise GameRuleError('Counter riders on split commander destinations require per-destination preparation')
    from .composition_replacements import validate_composition_replacements,composition_ordering_principal
    prepared=plan.prepared_replacement
    from .composition_replacements import capture_composed_card_zone_components
    from .replacement.composition_scope import event_card_components
    try:
        if event_card_components(prepared.event)!=capture_composed_card_zone_components(root):
            raise ValueError('Card-component replacement scope does not match the physical store')
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if root.is_token and storage.topology.commander_designation_ids:
        raise GameRuleError('Token-parent commander replacements require per-destination component choices')
    validate_composition_replacements(prepared)
    expected=composition_ordering_principal(owner.host,root,prepared,actor)
    if chooser is not None and chooser!=expected:raise GameRuleError('The physical order belongs to a different principal')
    if plan.prepared_life_payment is not None:
        raise GameRuleError('Composition replacement side effects require their represented group continuation')
    return root,storage,plan


def _physical_cards(root,storage):
    result={}
    for snapshot,component in zip(storage.parts,storage.topology.components):
        card=snapshot.detached_card()
        card.zone='battlefield';card.controller=root.controller
        card.is_token=component.is_token;card.object_kind='token' if component.is_token else 'card'
        card.is_commander=component.commander_designation_id is not None
        card.commander_designation_id=component.commander_designation_id
        card.attachments=[];card.attached_to=None;card.attacking=None;card.blocking=None
        if card.is_token:card.annotations['copy_overrides']=thaw_value(snapshot.copiable_values)
        result[card.object_id]=card
    return result


def _physical_departure(snapshot,parent_departure):
    return ZoneDepartureSnapshot(origin='battlefield',controller=parent_departure.controller,
        logical_object_id=snapshot.identity.logical_object_id,characteristics={},attachments=(),attached_to=None,
        trigger_sources=parent_departure.trigger_sources,counters={} if parent_departure.counters is not None else None)


def _parent_occurrence(root,departure,plan,count,reason):
    return ZoneChangeOccurrence(object_id=root.object_id,card_ref=root.ref,owner=root.owner,
        origin='battlefield',destination=plan.destination,previous_controller=departure.controller,
        current_controller=root.owner,previous_logical_object_id=departure.logical_object_id,
        current_logical_object_id=f'{root.object_id}@{root.zone_change_counter+1}',
        zone_change_counter=root.zone_change_counter+1,token=root.is_token,card_object=root.is_card_object,
        previous_characteristics=departure.characteristics,current_characteristics={},
        previous_copy_snapshot=departure.copy_snapshot,previous_attachments=departure.attachments,
        previous_attached_to=departure.attached_to,previous_counters=departure.counters,
        current_counters={} if departure.counters is not None else None,
        schema_version=2 if departure.counters is not None else 1,cause=reason,
        transition_kind=plan.prepared_replacement.transition_kind,
        composition=CompositionZoneFact(plan.card.permanent_composition.topology.object_identity,
            CompositionZoneRole.PERMANENT,count))



def commit_prepared_composition_departures(owner,batch):
    """Commit all prepared physical and ordinary arrivals before publication."""
    from .composition_departure_batch import PreparedCompositionDepartureBatch,publish_composition_departure_batch
    from .composition_replacements import project_composition_counters
    from .counter_placement import commit_counter_events_from_resolution
    from .entry_results import commit_prepared_entry_results
    if not isinstance(batch,PreparedCompositionDepartureBatch):
        raise GameRuleError('Physical departure requires its typed prepared batch')
    order=batch.request.ordered_ids(batch.orders)
    with owner.host.transaction():
        batch.validate_roots(owner)
        groups=[]
        for group in batch.groups:
            captured=owner._capture_departure(group.plan.card,semantic_events=True)
            before=group.departure
            groups.append(replace(group,departure=replace(captured,trigger_sources=before.trigger_sources,
                characteristics=before.characteristics,counters=before.counters,attachments=before.attachments,
                attached_to=before.attached_to,copy_snapshot=before.copy_snapshot if group.storage is not None else captured.copy_snapshot)))
        batch=replace(batch,groups=tuple(groups))
        committed=[];physical={};plans={};departures={};composed=set()
        for group in batch.groups:
            root=group.plan.card
            if group.storage is None:
                cards={root.object_id:root};before={root.object_id:group.departure}
            else:
                cards=_physical_cards(root,group.storage)
                snapshots={part.identity.object_id:part for part in group.storage.parts}
                before={oid:_physical_departure(snapshots[oid],group.departure) for oid in cards}
                composed.update(cards)
            members=tuple(oid for oid in order if oid in cards)
            committed.append((group,cards,before,members))
            physical.update(cards);departures.update(before)
            for oid in cards:
                plans[oid]=(replace(group.plan,card=cards[oid],prepared_life_payment=None,enter_face=None,
                    aura_entry_plan=None,prospective_battle_protector=None,manifest_entry=False,
                    destination=group.subject.component_destinations.get(oid,group.plan.destination))
                    if group.storage is not None else group.plan)
        owner.pin_characteristic_departures(tuple(group.plan.card for group in batch.groups))
        for group,_cards,_before,_members in committed:
            if group.storage is None:continue
            root=group.plan.card
            owner.host._remove_object_from_combat(root,reason='composed permanent left the battlefield')
            clear_object_attachment_relations(owner.state.cards,root,players=owner.state.players)
            owner.remove_from_zone(root)
        GameObjectStore(owner.host).commit(
            upserts=tuple(physical[oid] for oid in order if oid in composed),
            removals=tuple(group.plan.card.object_id for group in batch.groups if group.storage is not None))
        for oid in order:
            owner._commit_move(plans[oid],departures[oid],controller=None,tapped=None,enter_face=None,
                aura_target_ref=None,zone_timestamp=owner.next_timestamp(),reveal_to=(),already_detached=oid in composed)
        counter_results=[]
        for group,cards,_before,members in committed:
            if group.storage is not None:
                result=project_composition_counters(owner.host,group.plan.prepared_replacement,
                    tuple(cards[oid] for oid in members))
                commit_counter_events_from_resolution(owner.host,result,reason=batch.reason,log=False,
                    error_type=GameRuleError,dispatch_events=False)
            else:
                result=group.plan.prepared_replacement
                commit_prepared_entry_results(owner.host,result,group.plan.card,reason=batch.reason,
                    log=False,error_type=StateInvariantError)
            counter_results.append(result)
            log_applied_zone_replacements(owner.host,group.plan.prepared_replacement,group.plan.card,
                requested_destination=group.plan.requested_destination,error_type=StateInvariantError)
        publish_composition_departure_batch(owner,batch,tuple(committed),tuple(counter_results))
        for group,cards,_before,_members in committed:
            if group.storage is not None:
                owner.host._log(group.actor,'composition.departure',f'{group.plan.card.ref} separated into its physical parts.',
                    {'object':group.plan.card.ref,'count':len(cards),'to':group.plan.destination,'reason':batch.reason},importance=1)
        return tuple(physical[oid] for oid in order)


def depart_composition(owner,object_id,destination,*,physical_order,chooser,actor,reason,
        position='top',replacement_selections=(),transition_kind=ZoneTransitionKind.ORDINARY,prepared_replacement=None):
    """Adapt the existing single-object port to the complete physical batch."""
    from .composition_departure_batch import prepare_composition_departure_batch
    _root,_storage,plan=_prepare(owner,object_id,destination,physical_order,chooser,actor,reason,
        position,replacement_selections,transition_kind,prepared_replacement)
    if plan is None:return ()
    batch=prepare_composition_departure_batch(owner,((object_id,destination),),reason=reason,actor=actor,
        position=position,replacement_selections=replacement_selections,
        transition_kinds={object_id:transition_kind},
        prepared_replacements={object_id:prepared_replacement} if prepared_replacement is not None else None)
    if chooser is not None and chooser!=batch.groups[0].subject.ordering_actor:
        raise GameRuleError('The physical order belongs to a different principal')
    if not isinstance(physical_order,(list,tuple)) or any(type(oid) is not str for oid in physical_order):
        raise GameRuleError('A physical order must name each component')
    by_id={state['object_id']:state['ref'] for state in batch.groups[0].subject.physical_states}
    try:
        refs=tuple(by_id[oid] for oid in physical_order)
        if len(refs)!=len(by_id) or len(set(refs))!=len(refs):raise ValueError('Physical order must include every part once')
        orders=tuple(tuple(ref for ref in refs if ref in allowed) for _group,allowed in batch.request.choices)
        batch=batch.with_orders(orders)
    except (KeyError,ValueError) as exc:raise GameRuleError(str(exc)) from exc
    return commit_prepared_composition_departures(owner,batch)
