"""Prepare one physical departure batch and publish its completed facts."""
from dataclasses import dataclass,replace

from .composition_batch_order_model import CompositionDepartureSubject,CompositionDepartureBatchRequest
from .composition_departure import CompositionZoneFact,CompositionZoneRole
from .errors import GameRuleError
from .replacement.immutable import FrozenMap
from .zone_trigger_events import ZoneTransitionKind
from .composition_storage import PermanentCompositionStorage
from .zone_transition_model import ZoneMovePlan,ZoneDepartureSnapshot
from .composition_replacements import composition_component_destinations


@dataclass(frozen=True,slots=True)
class PreparedCompositionDepartureGroup:
    subject: CompositionDepartureSubject
    plan: ZoneMovePlan
    storage: PermanentCompositionStorage | None
    departure: ZoneDepartureSnapshot
    actor: str

    def __post_init__(self):
        if (not isinstance(self.subject,CompositionDepartureSubject) or not isinstance(self.plan,ZoneMovePlan)
                or not isinstance(self.departure,ZoneDepartureSnapshot)
                or self.storage is not None and not isinstance(self.storage,PermanentCompositionStorage)
                or type(self.actor) is not str or not self.actor
                or self.subject.card_state['object_id']!=self.plan.card.object_id
                or self.subject.requested_destination!=self.plan.requested_destination
                or self.subject.destination!=self.plan.destination
                or self.subject.transition_kind!=self.plan.prepared_replacement.transition_kind.value
                or self.plan.card.permanent_composition!=self.storage):
            raise ValueError('Prepared physical group does not match its typed source and destination')


@dataclass(frozen=True,slots=True)
class PreparedCompositionDepartureBatch:
    request: CompositionDepartureBatchRequest
    groups: tuple[PreparedCompositionDepartureGroup,...]
    reason: str
    orders: tuple[tuple[str,...],...] | None=None

    def __post_init__(self):
        if (not isinstance(self.request,CompositionDepartureBatchRequest)
                or not isinstance(self.groups,tuple) or not self.groups
                or any(not isinstance(group,PreparedCompositionDepartureGroup) for group in self.groups)
                or tuple(group.subject for group in self.groups)!=self.request.subjects
                or type(self.reason) is not str or not self.reason):
            raise ValueError('A prepared physical batch requires its complete typed subjects')
        if self.orders is not None:self.request.ordered_ids(self.orders)

    @property
    def changes(self):
        return tuple((group.subject.card_state['object_id'],group.subject.requested_destination) for group in self.groups)

    def with_orders(self,orders):
        return replace(self,orders=tuple(tuple(order) for order in orders))

    def validate_roots(self,owner):
        for group in self.groups:
            before=group.subject.card_state
            current=owner.state.cards.get(before['object_id'])
            if (current is None or current.logical_object_id!=group.departure.logical_object_id
                    or current.zone!='battlefield' or current.controller!=before['controller']
                    or current.owner!=before['owner'] or current.permanent_composition!=group.storage):
                raise GameRuleError('A prepared physical batch changed its current incarnation or physical store')


def prepare_composition_departure_batch(owner,changes,*,reason,transition_kinds=None,
        prepared_replacements=None,actor=None,position='top',replacement_selections=()):
    """Read all destinations, physical stores and LKI before offering any order."""
    from .composition_departure_commit import _prepare
    from .composition_replacements import composition_ordering_principal
    from .model import CardInstance
    from .token_creation import token_copy_snapshot
    from .zone_trigger_processing import capture_departure_trigger_sources
    changes=tuple(changes);kinds=dict(transition_kinds or {});prepared=dict(prepared_replacements or {})
    if len({oid for oid,_ in changes})!=len(changes):raise GameRuleError('Physical batch subjects must be distinct')
    if prepared and set(prepared)!={oid for oid,_ in changes}:
        raise GameRuleError('Physical batch replacements do not cover every subject')
    sources=capture_departure_trigger_sources(owner.host,semantic_events=True,origin='battlefield')
    groups=[]
    for object_id,destination in changes:
        root=owner.state.cards.get(object_id)
        if root is None or root.zone!='battlefield':raise GameRuleError('Physical departure requires current battlefield membership')
        current_actor=actor or root.controller
        owner.host._require_seat(current_actor,in_game=True)
        kind=kinds.get(object_id,ZoneTransitionKind.ORDINARY)
        storage=root.permanent_composition
        if storage is not None:
            _root,_storage,plan=_prepare(owner,object_id,destination,storage.topology.physical_object_ids,
                None,current_actor,reason,position,replacement_selections,kind,prepared.get(object_id))
        else:
            plan=owner._prepare_move(root,destination,controller=None,tapped=None,entry_pay_life=False,
                enter_face=None,battle_protector=None,aura_target_ref=None,resolving_as_aura_spell=False,
                aura_enchant_spec=None,position=position,reason=reason,log=False,
                replacement_selections=replacement_selections,prepared_replacement=prepared.get(object_id),transition_kind=kind)
        if plan is None or isinstance(plan,CardInstance) or plan.destination=='battlefield' or plan.prepared_life_payment is not None:
            raise GameRuleError('Physical batch requires represented departures without entry or life-payment side effects')
        chooser=composition_ordering_principal(owner.host,root,plan.prepared_replacement,current_actor)
        departure=owner._simultaneous_departure(root,source_snapshot=sources)[1]
        if storage is not None:
            if departure.conditional_returns:
                raise GameRuleError('Linked composition departures require their represented group continuation')
            departure=replace(departure,copy_snapshot=token_copy_snapshot(owner.host,root))
        redirects,actors=composition_component_destinations(owner.host,root,plan.prepared_replacement,current_actor)
        subject=CompositionDepartureSubject(FrozenMap(root.to_dict()),destination,plan.destination,chooser,
            plan.library_position or 'top',kind.value,redirects,actors)
        groups.append(PreparedCompositionDepartureGroup(subject,plan,storage,departure,current_actor))
    request=CompositionDepartureBatchRequest(tuple(group.subject for group in groups),tuple(owner.host.apnap_order()))
    return PreparedCompositionDepartureBatch(request,tuple(groups),reason)


def _group_facts(owner,group,cards,departures,order,reason):
    from .composition_departure_commit import _parent_occurrence
    kind=ZoneTransitionKind(group.subject.transition_kind)
    if group.storage is None:
        card=group.plan.card
        return ((owner._zone_change_occurrence(card,departure=group.departure,destination=card.zone,
            reason=reason,transition_kind=kind,read_ahead_chapter=group.plan.prepared_replacement.read_ahead_chapter),card),)
    root=group.plan.card
    parent=replace(_parent_occurrence(root,group.departure,group.plan,len(cards),reason),transition_kind=kind)
    facts=[(parent,cards.get(root.object_id,root))]
    for object_id in order:
        card=cards[object_id]
        fact=owner._zone_change_occurrence(card,departure=departures[object_id],destination=card.zone,
            reason=reason,transition_kind=ZoneTransitionKind.ORDINARY,read_ahead_chapter=None)
        facts.append((replace(fact,composition=CompositionZoneFact(parent.composition.parent,
            CompositionZoneRole.COMPONENT,len(cards))),card))
    return tuple(facts)


def publish_composition_departure_batch(owner,batch,committed,counter_results):
    """Publish histories for every subject before discovering any batch trigger."""
    from .control_effects import synchronize_control_effects
    from .rules.city_blessing import synchronize_citys_blessing
    from .soulbond_state import end_pairs_containing,reconcile_soulbond_pairs
    from .zone_trigger_processing import record_zone_change_history,dispatch_zone_change_occurrence
    from .counter_placement_events import dispatch_counter_event_trees
    from .rules.state_triggers import observe_public_state_triggers
    from .trigger_processing import enqueue_trigger_batch
    synchronize_control_effects(owner.host,reason='physical departure batch committed')
    synchronize_citys_blessing(owner.host,reason='physical departure batch before trigger facts')
    end_pairs_containing(owner.host,(group.plan.card.object_id for group in batch.groups),reason='physical departure batch')
    reconcile_soulbond_pairs(owner.host)
    facts=tuple((fact,card,group.departure.trigger_sources) for group,cards,departures,order in committed
        for fact,card in _group_facts(owner,group,cards,departures,order,batch.reason))
    for fact,_card,_sources in facts:record_zone_change_history(owner.host,fact)
    triggers=[]
    for fact,card,sources in facts:
        dispatch_zone_change_occurrence(owner.host,fact,card,departure_sources=sources.sources,
            departure_source_zones=sources.source_zones,departure_source_characteristics=sources.source_characteristics,
            trigger_batch=triggers,history_pre_recorded=True)
    dispatch_counter_event_trees(owner.host,tuple(counter_results),reason=batch.reason,trigger_batch=triggers)
    observe_public_state_triggers(owner.host,trigger_batch=triggers)
    enqueue_trigger_batch(owner.host,triggers)
    from .conditional_exile_return import return_from_departure_snapshots
    return_from_departure_snapshots(owner.host,tuple((group.plan.card,group.departure)
        for group in batch.groups if group.storage is None))
