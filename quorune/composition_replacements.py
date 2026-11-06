from __future__ import annotations

"""Project represented whole-object destination results onto physical parts."""

from dataclasses import replace
from .counter_permission import CounterPermissionSnapshot
from .errors import GameRuleError
from .replacement import AffectedObject
from .replacement.immutable import FrozenMap,thaw_value
from .replacement.operations import operation_to_dict


def capture_composed_card_zone_components(card):
    from .composition_zone_replacement_model import CardZoneComponent
    storage=card.permanent_composition
    if storage is None or not card.is_token:return ()
    storage.validate_carrier(card)
    return tuple(CardZoneComponent(part.identity.object_id,part.identity.logical_object_id,part.card_state['ref'])
        for part,component in zip(storage.parts,storage.topology.components) if not component.is_token)


def composition_card_preparation(prepared):
    from .replacement.composition_scope import card_component_event
    try:event=card_component_event(prepared.event)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if event is None:return None
    return replace(prepared,event=event,destination=event.payload['destination'],counter_events=tuple(event.children))


def composition_component_destinations(host,root,prepared,actor):
    from .composition_commander_replacement_model import commander_component_destinations
    from .replacement.composition_scope import event_card_components
    destinations=commander_component_destinations(prepared.event.payload)
    actors={}
    group=composition_card_preparation(prepared)
    if group is not None:
        chooser=composition_ordering_principal(host,root,group,actor)
        for component in event_card_components(prepared.event):
            destinations[component.object_id]=group.destination
            actors[component.object_id]=chooser
    return FrozenMap(destinations),FrozenMap(actors)


def validate_composition_replacements(prepared):
    """Keep unsupported child modifiers out of the physical commit boundary."""
    if prepared.keyword_grants or prepared.devour_sacrifices:
        raise GameRuleError('Composition entry riders require their represented group continuation')
    event=prepared.event
    if event is None:raise GameRuleError('Composition replacement event is missing')
    effects={effect.effect_id:effect for effect in prepared.effects}
    # CR 614.7. Available replacements are inert without a counter event.
    if prepared.counter_events and any(effect.event_kind=='counter.place' for effect in prepared.effects):
        raise GameRuleError('Per-part counter replacements require their represented group continuation')
    for effect_id in event.applied_effects:
        effect=effects.get(effect_id)
        from .composition_commander_replacement_model import payload_commander_components,RETURNS_FIELD
        components=payload_commander_components(event.payload)
        commander=next((row for row in components if effect is not None
            and effect.source_id==f'rule:903.9b:{row.designation_id}'),None)
        if commander is not None:
            operations=tuple(operation_to_dict(operation) for operation in effect.operations)
            if (effect.event_kind!='zone.change' or effect.optional is not True
                    or operations!=({'op':'union','field':RETURNS_FIELD,'values':[commander.designation_id]},)):
                raise GameRuleError('Commander component replacement operation is malformed')
            continue
        if effect is None or effect.event_kind!='zone.change' or effect.optional:
            raise GameRuleError('Composition replacement result has an unsupported source')
        for operation in effect.operations:
            value=operation_to_dict(operation)
            if value.get('op')=='set' and value.get('field')=='destination':continue
            if value.get('op')=='create_affected_object_counter':continue
            raise GameRuleError('Composition replacement operation is unrepresented')
    if tuple(event.children)!=tuple(prepared.counter_events):
        raise GameRuleError('Composition replacement children must be fixed counter leaves')
    for child in prepared.counter_events:
        if (child.kind!='counter.place' or child.children or child.applied_effects
                or child.affected_object is None or child.affected_object.object_id!=prepared.object_id
                or child.payload.get('follows_zone_destination') is not True
                or child.payload.get('target_zone')!=prepared.destination
                or type(child.payload.get('requested_amount')) is not int or child.payload['requested_amount']<1
                or type(child.payload.get('amount')) is not int
                or child.payload.get('amount')!=child.payload.get('requested_amount')
                or child.payload.get('_counter_prohibited') is True):
            raise GameRuleError('Composition counter result requires independent per-part preparation')
        permissions=child.payload.get('_counter_permissions')
        if permissions is not None and CounterPermissionSnapshot.from_dict(thaw_value(permissions)).rules:
            raise GameRuleError('Composition counter permissions require independent per-part preparation')
    group=composition_card_preparation(prepared)
    if group is not None:validate_composition_replacements(group)


def composition_ordering_principal(host,root,prepared,actor):
    if prepared.destination in ('graveyard','library','hand','command'):return root.owner
    chooser=actor
    if prepared.destination!='exile':return chooser
    by_id={effect.effect_id:effect for effect in prepared.effects}
    for effect_id in prepared.event.applied_effects:
        effect=by_id.get(effect_id)
        if effect is None:raise GameRuleError('Applied composition replacement source is missing')
        if any(operation_to_dict(operation)=={'op':'set','field':'destination','value':'exile'}
                for operation in effect.operations):
            sources=tuple(card for card in host.state.cards.values() if card.ref==effect.source_id)
            if len(sources)!=1 or sources[0].controller not in host.active_seats:
                raise GameRuleError('Composition exiling player is unavailable')
            chooser=sources[0].controller
    return chooser


def project_composition_counters(host,prepared,cards):
    """Build resolved counter leaves for each new physical incarnation."""
    validate_composition_replacements(prepared)
    from .replacement.composition_scope import event_card_components
    group=composition_card_preparation(prepared)
    card_ids={row.object_id for row in event_card_components(prepared.event)}
    events=[]
    for card in cards:
        scoped=group if group is not None and card.object_id in card_ids else prepared
        types=host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]
        for child in scoped.counter_events:
            payload=thaw_value(child.payload)
            payload.update(target_zone=card.zone,target_kind='card',target_types=sorted(types),
                target_controller=None,target_logical_object_id=card.logical_object_id)
            payload.pop('prospective_subject',None)
            events.append(replace(child,event_id=f'{child.event_id}:component:{card.logical_object_id}',
                affected_object=AffectedObject(card.object_id,card.owner,None),payload=payload))
    return replace(prepared,event=replace(prepared.event,children=tuple(events)),counter_events=tuple(events))
