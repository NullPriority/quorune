from __future__ import annotations

"""Immutable physical-order request bound to one retained live composition."""

from collections.abc import Mapping
from dataclasses import dataclass,field
from .composition_batch_order_model import CompositionDepartureBatchRequest
from .replacement.immutable import FrozenMap,thaw_value


@dataclass(frozen=True,slots=True)
class CompositionOrderRequest:
    root_state: FrozenMap
    destination: str
    actor: str
    chooser: str
    reason: str
    position: str | int='top'
    transition_kind: str='ordinary'
    schema_version: int=1
    resolved_destination: str | None=None
    ordering_actor: str | None=None
    component_destinations: FrozenMap=field(default_factory=FrozenMap)
    batch_request: CompositionDepartureBatchRequest | None=None
    orders: tuple[tuple[str,...],...]=()

    def __post_init__(self):
        from .model import CardInstance
        if (not isinstance(self.root_state,Mapping) or type(self.schema_version) is not int or self.schema_version not in (1,2,3,4)
                or self.destination not in ('graveyard','library','hand','exile','command','outside')
                or self.transition_kind not in ('ordinary','sacrifice')
                or any(type(value) is not str or not value for value in (self.actor,self.chooser,self.reason))
                or type(self.position) not in (str,int)
                or type(self.position) is int and self.position<1
                or type(self.position) is str and self.position not in ('top','bottom')):
            raise ValueError('Composition ordering requires its closed current request')
        if self.schema_version==1:
            if self.resolved_destination is not None or self.ordering_actor is not None:
                raise ValueError('Resolved composition destinations require request version two')
        elif (self.resolved_destination not in ('graveyard','library','hand','exile','command','outside')
                or type(self.ordering_actor) is not str or not self.ordering_actor):
            raise ValueError('Resolved composition ordering requires its destination and actor')
        state=FrozenMap(self.root_state)
        try:root=CardInstance.from_dict(thaw_value(state))
        except (TypeError,ValueError,KeyError) as exc:raise ValueError('Composition ordering root is malformed') from exc
        if root.permanent_composition is None or root.zone!='battlefield' or len(root.permanent_composition.parts)<2:
            raise ValueError('Composition ordering requires a retained live physical store')
        if FrozenMap(root.to_dict())!=state:raise ValueError('Composition ordering root must be canonical')
        final=self.resolved_destination or self.destination
        expected=root.owner if final in ('graveyard','library','hand','command') else self.ordering_actor or self.actor
        if self.schema_version==4:
            batch=self.batch_request
            if (not isinstance(batch,CompositionDepartureBatchRequest) or len(batch.subjects)!=1
                    or batch.subjects[0].card_state!=state or batch.subjects[0].requested_destination!=self.destination
                    or batch.subjects[0].destination!=self.resolved_destination
                    or batch.subjects[0].position!=self.position or batch.subjects[0].transition_kind!=self.transition_kind
                    or not batch.subjects[0].component_ordering_actors
                    or not isinstance(self.orders,tuple) or len(self.orders)>=len(batch.choices)):
                raise ValueError('Component ordering requires its exact single-subject departure batch')
            for (_group,refs),order in zip(batch.choices,self.orders):
                if not isinstance(order,tuple) or len(order)!=len(refs) or len(set(order))!=len(order) or set(order)!=set(refs):
                    raise ValueError('Saved component order is not an issued permutation')
            expected=batch.choices[len(self.orders)][0][0]
            if self.ordering_actor!=expected:raise ValueError('Component ordering actor does not own the current group')
        elif self.batch_request is not None or self.orders:
            raise ValueError('Batched component ordering requires schema four')
        if self.chooser!=expected:raise ValueError('Composition ordering principal does not own this destination choice')
        object.__setattr__(self,'root_state',state)
        redirects=FrozenMap(self.component_destinations)
        if self.schema_version!=3 and redirects:raise ValueError('Component destinations require ordering schema three')
        physical={part.identity.object_id:part.card_state for part in root.permanent_composition.parts}
        if any(oid not in physical or zone!='command' or physical[oid]['is_commander'] is not True
                or not physical[oid].get('commander_designation_id') for oid,zone in redirects.items()):
            raise ValueError('Component command destinations must name stored physical commanders')
        object.__setattr__(self,'component_destinations',redirects)

    @property
    def root(self):
        from .model import CardInstance
        return CardInstance.from_dict(thaw_value(self.root_state))

    @property
    def refs(self):
        if self.schema_version==4:return self.batch_request.choices[len(self.orders)][1]
        return tuple(part.card_state['ref'] for part in self.root.permanent_composition.parts
            if part.identity.object_id not in self.component_destinations)

    @property
    def current_destination(self):
        if self.schema_version==4:return self.batch_request.choices[len(self.orders)][0][1]
        return self.resolved_destination or self.destination

    def validate_order(self,refs):
        if (not isinstance(refs,(list,tuple)) or any(type(ref) is not str for ref in refs)
                or len(refs)!=len(set(refs)) or len(refs)!=len(self.refs) or set(refs)!=set(self.refs)):
            raise ValueError('Order every stored physical part exactly once')

    def ordered_ids(self,refs):
        self.validate_order(refs)
        if self.schema_version==4:return self.batch_request.ordered_ids((*self.orders,tuple(refs)))
        by_ref={part.card_state['ref']:part.identity.object_id for part in self.root.permanent_composition.parts}
        parts=self.root.permanent_composition.parts;chosen=iter(refs)
        return tuple(part.identity.object_id if part.identity.object_id in self.component_destinations
            else by_ref[next(chosen)] for part in parts)

    def validate_current(self,state):
        root=state.cards.get(self.root_state['object_id'])
        if root is None or FrozenMap(root.to_dict())!=self.root_state:
            raise ValueError('The ordered composition changed before completion')

    def to_dict(self):
        result={'schema_version':self.schema_version,'root_state':thaw_value(self.root_state),
            'destination':self.destination,'actor':self.actor,'chooser':self.chooser,'reason':self.reason,
            'position':self.position,'transition_kind':self.transition_kind}
        if self.schema_version in {2,3,4}:result.update(resolved_destination=self.resolved_destination,ordering_actor=self.ordering_actor)
        if self.schema_version==3:result['component_destinations']=thaw_value(self.component_destinations)
        if self.schema_version==4:result.update(batch_request=self.batch_request.to_dict(),orders=[list(row) for row in self.orders])
        return result

    @classmethod
    def from_dict(cls,value):
        fields={'schema_version','root_state','destination','actor','chooser','reason','position','transition_kind'}
        if isinstance(value,Mapping) and value.get('schema_version') in {2,3,4}:fields|={'resolved_destination','ordering_actor'}
        if isinstance(value,Mapping) and value.get('schema_version')==3:fields|={'component_destinations'}
        if isinstance(value,Mapping) and value.get('schema_version')==4:fields|={'batch_request','orders'}
        if not isinstance(value,Mapping) or set(value)!=fields:
            raise ValueError('Composition ordering has a closed wire shape')
        if value['schema_version']==4 and (not isinstance(value['orders'],(list,tuple))
                or any(not isinstance(row,(list,tuple)) for row in value['orders'])):
            raise ValueError('Component orders must be arrays of issued references')
        return cls(value['root_state'],value['destination'],value['actor'],value['chooser'],value['reason'],
            value['position'],value['transition_kind'],value['schema_version'],
            value.get('resolved_destination'),value.get('ordering_actor'),value.get('component_destinations',FrozenMap()),
            CompositionDepartureBatchRequest.from_dict(value['batch_request']) if value['schema_version']==4 else None,
            tuple(tuple(row) for row in value['orders']) if value['schema_version']==4 else ())


class CompositionOrderChoiceRequired(Exception):
    def __init__(self,request:CompositionOrderRequest):
        if not isinstance(request,CompositionOrderRequest):raise ValueError('Composition order request must be typed')
        self.request=request
        super().__init__('The physical owner must order this departing composition')
