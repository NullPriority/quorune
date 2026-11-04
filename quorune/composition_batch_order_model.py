"""Closed physical ordering for a complete simultaneous departure batch."""
from collections.abc import Mapping
from dataclasses import dataclass,replace,field

from .model import CardInstance
from .replacement.immutable import FrozenMap,thaw_value


@dataclass(frozen=True,slots=True)
class CompositionDepartureSubject:
    card_state: FrozenMap
    requested_destination: str
    destination: str
    ordering_actor: str
    position: str | int='top'
    transition_kind: str='ordinary'
    component_destinations: FrozenMap=field(default_factory=FrozenMap)
    component_ordering_actors: FrozenMap=field(default_factory=FrozenMap)

    def __post_init__(self):
        zones={'graveyard','library','hand','exile','command','outside'}
        if (not isinstance(self.card_state,Mapping) or self.requested_destination not in zones
                or self.destination not in zones or type(self.ordering_actor) is not str or not self.ordering_actor
                or self.transition_kind not in {'ordinary','sacrifice'}
                or type(self.position) not in {str,int}
                or type(self.position) is int and self.position<1
                or type(self.position) is str and self.position not in {'top','bottom'}):
            raise ValueError('Composition batch subject fields are malformed')
        state=FrozenMap(self.card_state)
        try:card=CardInstance.from_dict(thaw_value(state))
        except (ValueError,TypeError,KeyError) as exc:raise ValueError('Composition subject card is malformed') from exc
        if (card.zone!='battlefield' or FrozenMap(card.to_dict())!=state
                or any(type(value) is not str or not value for value in (card.object_id,card.ref,card.owner,card.controller))):
            raise ValueError('Composition batch subjects require canonical battlefield objects')
        if self.destination in {'graveyard','library','hand','command'} and self.ordering_actor!=card.owner:
            raise ValueError('The physical owner orders this destination')
        object.__setattr__(self,'card_state',state)
        redirects=FrozenMap(self.component_destinations)
        actors=FrozenMap(self.component_ordering_actors)
        physical={raw['object_id']:raw for raw in self.physical_states}
        storage=card.permanent_composition
        card_ids=({row.identity.object_id for row in storage.topology.components if not row.is_token}
            if storage is not None and card.is_token else set())
        scoped={oid for oid,destination in redirects.items() if destination!='command'}
        if (scoped and scoped!=card_ids or set(actors)!=scoped
                or len({redirects[oid] for oid in scoped})>1 or len(set(actors.values()))>1):
            raise ValueError('Physical card destinations and actors require the complete token-parent card scope')
        for oid,destination in redirects.items():
            if oid not in physical or destination not in zones:
                raise ValueError('Component destinations must name stored physical objects and supported zones')
            if destination=='command':
                if physical[oid]['is_commander'] is not True or not physical[oid].get('commander_designation_id'):
                    raise ValueError('Only stored physical commanders may have a component command destination')
            elif (type(actors[oid]) is not str or not actors[oid]
                    or destination in {'graveyard','library','hand'} and actors[oid]!=card.owner):
                raise ValueError('Card-component destination ordering belongs to its rules principal')
        object.__setattr__(self,'component_destinations',redirects)
        object.__setattr__(self,'component_ordering_actors',actors)

    @property
    def physical_states(self):
        card=CardInstance.from_dict(thaw_value(self.card_state))
        return (tuple(part.card_state for part in card.permanent_composition.parts)
                if card.permanent_composition is not None else (self.card_state,))

    @property
    def group(self):
        return self.ordering_actor,self.destination,self.position

    def group_for(self,state):
        destination=self.component_destinations.get(state['object_id'],self.destination)
        return (state['owner'] if destination=='command' else self.component_ordering_actors.get(state['object_id'],self.ordering_actor)),destination,self.position

    def to_dict(self):
        return {**({'component_destinations':thaw_value(self.component_destinations)} if self.component_destinations else {}),
            **({'component_ordering_actors':thaw_value(self.component_ordering_actors)} if self.component_ordering_actors else {}),
            'card_state':thaw_value(self.card_state),'requested_destination':self.requested_destination,
            'destination':self.destination,'ordering_actor':self.ordering_actor,
            'position':self.position,'transition_kind':self.transition_kind}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)-{'component_destinations','component_ordering_actors'}!={'card_state','requested_destination','destination',
                'ordering_actor','position','transition_kind'}:
            raise ValueError('Composition batch subjects have a closed schema')
        return cls(**value)


@dataclass(frozen=True,slots=True)
class CompositionDepartureBatchRequest:
    subjects: tuple[CompositionDepartureSubject,...]
    apnap_order: tuple[str,...]
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.subjects,tuple) or not self.subjects
                or any(not isinstance(subject,CompositionDepartureSubject) for subject in self.subjects)
                or not isinstance(self.apnap_order,tuple) or not self.apnap_order
                or any(type(seat) is not str or not seat for seat in self.apnap_order)
                or len(self.apnap_order)!=len(set(self.apnap_order))):
            raise ValueError('Composition departure batches require their closed current subjects and APNAP order')
        physical=[state for subject in self.subjects for state in subject.physical_states]
        if not any(subject.card_state.get('permanent_composition') is not None for subject in self.subjects):
            raise ValueError('A physical batch must contain a composed permanent')
        for field in ('object_id','ref'):
            values=[state[field] for state in physical]
            if len(values)!=len(set(values)):raise ValueError('Physical departure identities must be unique')
        if any(actor not in self.apnap_order for subject in self.subjects
                for actor in (subject.ordering_actor,*subject.component_ordering_actors.values())):
            raise ValueError('A physical ordering actor is not a surviving participant')

    @property
    def groups(self):
        result={}
        for subject in self.subjects:
            for state in subject.physical_states:result.setdefault(subject.group_for(state),[]).append(state['ref'])
        return tuple((group,tuple(result[group])) for seat in self.apnap_order
            for group in result if group[0]==seat)

    @property
    def choices(self):
        return tuple((group,refs) for group,refs in self.groups if len(refs)>1 and group[1] not in {'hand','command','outside'})

    def ordered_ids(self,orders):
        if not isinstance(orders,(list,tuple)) or len(orders)!=len(self.choices):
            raise ValueError('Every simultaneous physical ordering choice is required')
        chosen={}
        for (group,refs),order in zip(self.choices,orders):
            if (not isinstance(order,(list,tuple)) or any(type(ref) is not str for ref in order)
                    or len(order)!=len(refs) or len(set(order))!=len(order) or set(order)!=set(refs)):
                raise ValueError('Order every physical reference exactly once')
            chosen[group]=tuple(order)
        states={state['ref']:state for subject in self.subjects for state in subject.physical_states}
        refs=[state['ref'] for subject in self.subjects for state in subject.physical_states]
        for group,initial in self.groups:
            order=chosen.get(group,initial)
            if group[1]=='library' and group[2]=='bottom':order=tuple(reversed(order))
            indices=[index for index,ref in enumerate(refs) if ref in initial]
            for index,ref in zip(indices,order):refs[index]=ref
        return tuple(states[ref]['object_id'] for ref in refs)

    def to_dict(self):
        return {'schema_version':self.schema_version,'subjects':[subject.to_dict() for subject in self.subjects],
            'apnap_order':list(self.apnap_order)}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','subjects','apnap_order'}
                or not isinstance(value['subjects'],(list,tuple)) or not isinstance(value['apnap_order'],(list,tuple))):
            raise ValueError('Composition departure batches have a closed wire shape')
        return cls(tuple(CompositionDepartureSubject.from_dict(subject) for subject in value['subjects']),
            tuple(value['apnap_order']),value['schema_version'])


@dataclass(frozen=True,slots=True)
class CompositionStateBasedOrderContinuation:
    request: CompositionDepartureBatchRequest
    state_based_frame: FrozenMap
    destruction_selections: tuple
    zone_selections: tuple
    orders: tuple[tuple[str,...],...]=()
    schema_version: int=1

    def __post_init__(self):
        fields={'active_player','phase','step','turn_sequence','stack_refs','battlefield','turn_order','active_seats'}
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.request,CompositionDepartureBatchRequest)
                or not isinstance(self.state_based_frame,Mapping) or set(self.state_based_frame)!=fields
                or not isinstance(self.orders,tuple) or len(self.orders)>len(self.request.choices)):
            raise ValueError('Composition state-based continuation fields are malformed')
        frame=FrozenMap(self.state_based_frame)
        if (type(frame['turn_sequence']) is not int or frame['turn_sequence']<0
                or any(not isinstance(frame[field],tuple) for field in ('active_seats','turn_order','stack_refs','battlefield'))
                or any(type(frame[field]) is not str or not frame[field] for field in ('active_player','phase','step'))
                or len(frame['turn_order'])!=len(set(frame['turn_order']))
                or len(frame['active_seats'])!=len(set(frame['active_seats']))
                or set(frame['active_seats'])-set(frame['turn_order'])
                or frame['active_player'] not in frame['turn_order']
                or set(frame['active_seats'])!=set(self.request.apnap_order)):
            raise ValueError('Composition state-based turn or participants are malformed')
        rows=frame['battlefield']
        if any(not isinstance(row,Mapping) or 'object_id' not in row or 'logical_object_id' not in row for row in rows):
            raise ValueError('Composition battlefield identities are malformed')
        indexed={row['object_id']:row for row in rows}
        if len(indexed)!=len(rows):raise ValueError('Composition battlefield identities must be unique')
        for subject in self.request.subjects:
            card=CardInstance.from_dict(thaw_value(subject.card_state))
            if indexed.get(card.object_id,{}).get('logical_object_id')!=card.logical_object_id:
                raise ValueError('Composition subjects do not match their captured battlefield incarnations')
        for field in ('destruction_selections','zone_selections'):
            values=getattr(self,field)
            if not isinstance(values,tuple) or any(not isinstance(value,(str,Mapping)) for value in values):
                raise ValueError('Composition replacement selections are malformed')
            object.__setattr__(self,field,tuple(FrozenMap(value) if isinstance(value,Mapping) else value for value in values))
        for (_group,refs),order in zip(self.request.choices,self.orders):
            if (not isinstance(order,tuple) or any(type(ref) is not str for ref in order)
                    or len(order)!=len(refs) or len(set(order))!=len(order) or set(order)!=set(refs)):
                raise ValueError('Collected composition orders must be exact permutations')
        object.__setattr__(self,'state_based_frame',frame)

    @property
    def current_choice(self):
        return self.request.choices[len(self.orders)] if len(self.orders)<len(self.request.choices) else None

    def with_order(self,refs):
        if self.current_choice is None or not isinstance(refs,(list,tuple)):
            raise ValueError('No remaining physical order accepts this response')
        return replace(self,orders=(*self.orders,tuple(refs)))

    def to_dict(self):
        return {'schema_version':self.schema_version,'request':self.request.to_dict(),
            'state_based_frame':thaw_value(self.state_based_frame),
            'destruction_selections':[thaw_value(value) for value in self.destruction_selections],
            'zone_selections':[thaw_value(value) for value in self.zone_selections],
            'orders':[list(order) for order in self.orders]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','request','state_based_frame',
                'destruction_selections','zone_selections','orders'}
                or any(not isinstance(value[field],(list,tuple)) for field in ('destruction_selections','zone_selections','orders'))
                or any(not isinstance(order,(list,tuple)) for order in value['orders'])):
            raise ValueError('Composition state-based continuations have a closed schema')
        return cls(CompositionDepartureBatchRequest.from_dict(value['request']),value['state_based_frame'],
            tuple(value['destruction_selections']),tuple(value['zone_selections']),
            tuple(tuple(order) for order in value['orders']),value['schema_version'])
