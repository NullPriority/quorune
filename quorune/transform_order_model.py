from __future__ import annotations

"""Closed retained data for a simultaneous transform timestamp choice."""

from collections.abc import Mapping
from dataclasses import dataclass,replace

from .replacement.immutable import FrozenMap,thaw_value


class TransformTimestampModelError(ValueError):
    """A saved transform-order value violates its closed domain schema."""


@dataclass(frozen=True,slots=True)
class TransformTimestampRequest:
    subjects: tuple[FrozenMap,...]
    reason: str
    day_night_instruction: bool
    expected_counts: FrozenMap

    def __post_init__(self):
        from .model import CardInstance
        if (not isinstance(self.subjects,(list,tuple)) or type(self.reason) is not str or not self.reason
                or type(self.day_night_instruction) is not bool or not isinstance(self.expected_counts,Mapping)):
            raise TransformTimestampModelError('Transform timestamp request is malformed')
        try:
            subjects=tuple(FrozenMap(value) for value in self.subjects)
            cards=tuple(CardInstance.from_dict(thaw_value(value)) for value in subjects)
        except (TypeError,ValueError,KeyError) as error:raise TransformTimestampModelError('Transform timestamp subject data is malformed') from error
        if (any(card.zone!='battlefield' or FrozenMap(card.to_dict())!=raw for card,raw in zip(cards,subjects))
                or len({card.object_id for card in cards})!=len(cards)
                or len({card.ref for card in cards})!=len(cards)):
            raise TransformTimestampModelError('Transform timestamp subjects must be canonical current permanents')
        controllers=[card.controller for card in cards]
        seen=[]
        for actor in controllers:
            if not seen or seen[-1]!=actor:
                if actor in seen:raise TransformTimestampModelError('Transform timestamp controller groups must be contiguous')
                seen.append(actor)
        counts=FrozenMap(self.expected_counts)
        if (set(counts)-{card.object_id for card in cards}
                or any(type(value) is not int or value<0 for value in counts.values())):
            raise TransformTimestampModelError('Transform timestamp count snapshots are malformed')
        object.__setattr__(self,'subjects',subjects);object.__setattr__(self,'expected_counts',counts)

    @property
    def groups(self):
        actors=tuple(dict.fromkeys(value['controller'] for value in self.subjects))
        return tuple((actor,tuple(value['ref'] for value in self.subjects if value['controller']==actor)) for actor in actors)

    @property
    def choices(self):return tuple((actor,refs) for actor,refs in self.groups if len(refs)>1)

    def validate_current(self,host):
        from .transform_timestamp_model import ordered_transform_cards
        cards=[]
        for raw in self.subjects:
            card=host.state.cards.get(raw['object_id'])
            if card is None or FrozenMap(card.to_dict())!=raw:
                raise TransformTimestampModelError('Transform timestamp subject changed while ordering')
            cards.append(card)
        if tuple(cards)!=ordered_transform_cards(cards,host.apnap_order()):
            raise TransformTimestampModelError('Transform timestamp APNAP order changed')

    def to_dict(self):
        return {'subjects':[thaw_value(value) for value in self.subjects],'reason':self.reason,
            'day_night_instruction':self.day_night_instruction,'expected_counts':thaw_value(self.expected_counts)}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'subjects','reason','day_night_instruction','expected_counts'}
                or not isinstance(value['subjects'],list) or any(not isinstance(item,Mapping) for item in value['subjects'])):
            raise TransformTimestampModelError('Transform timestamp request has a closed schema')
        return cls(tuple(value['subjects']),value['reason'],value['day_night_instruction'],value['expected_counts'])


class TransformTimestampOrderRequired(Exception):
    def __init__(self,request):
        self.request=request
        super().__init__('Controller timestamp order is required')


@dataclass(frozen=True,slots=True)
class TransformTimestampContinuation:
    request: TransformTimestampRequest
    context: FrozenMap
    resume_kind: str
    priority_player: str | None
    held_triggers: tuple[FrozenMap,...]
    orders: tuple[tuple[str,...],...]=()

    def __post_init__(self):
        from .model import StackItem
        if (not isinstance(self.request,TransformTimestampRequest) or not isinstance(self.context,Mapping)
                or self.resume_kind not in {'untap','stabilize'}
                or self.priority_player is not None and type(self.priority_player) is not str):
            raise TransformTimestampModelError('Transform timestamp continuation is malformed')
        context=FrozenMap(self.context)
        if (set(context)!={'active_player','turn_sequence','phase_index','phase','step'}
                or any(type(context[key]) is not int or context[key]<0 for key in ('turn_sequence','phase_index'))
                or any(type(context[key]) is not str or not context[key] for key in ('phase','step'))
                or context['active_player'] is not None and type(context['active_player']) is not str
                or self.resume_kind=='untap' and (context['phase']!='beginning' or context['step']!='untap' or context['phase_index']!=0)):
            raise TransformTimestampModelError('Transform timestamp turn context is malformed')
        triggers=tuple(FrozenMap(value) for value in self.held_triggers)
        trigger_ids=[];trigger_refs=[]
        for value in triggers:
            try:item=StackItem.from_dict(thaw_value(value))
            except (TypeError,ValueError,KeyError) as error:
                raise TransformTimestampModelError('Transform timestamp held trigger data is malformed') from error
            if item.kind!='triggered_ability' or FrozenMap(item.to_dict())!=value:
                raise TransformTimestampModelError('Transform timestamp held trigger is malformed')
            trigger_ids.append(item.stack_id);trigger_refs.append(item.ref)
        if len(trigger_ids)!=len(set(trigger_ids)) or len(trigger_refs)!=len(set(trigger_refs)):
            raise TransformTimestampModelError('Transform timestamp held triggers must be unique')
        orders=tuple(tuple(value) for value in self.orders)
        if len(orders)>len(self.request.choices):raise TransformTimestampModelError('Too many transform timestamp orders')
        for order,(_actor,refs) in zip(orders,self.request.choices):
            self.validate_order(order,refs)
        object.__setattr__(self,'context',context);object.__setattr__(self,'held_triggers',triggers)
        object.__setattr__(self,'orders',orders)

    @staticmethod
    def validate_order(order,refs):
        if (not isinstance(order,(list,tuple)) or any(type(value) is not str for value in order)
                or len(order)!=len(set(order)) or len(order)!=len(refs) or set(order)!=set(refs)):
            raise TransformTimestampModelError('Order every transform timestamp subject exactly once')

    @property
    def current_choice(self):
        return self.request.choices[len(self.orders)] if len(self.orders)<len(self.request.choices) else None

    def with_order(self,order):
        choice=self.current_choice
        if choice is None:raise TransformTimestampModelError('Transform timestamp orders are already complete')
        self.validate_order(order,choice[1])
        return replace(self,orders=(*self.orders,tuple(order)))

    @property
    def ordered_ids(self):
        if self.current_choice is not None:raise TransformTimestampModelError('Transform timestamp orders are incomplete')
        selected=dict(zip((actor for actor,_refs in self.request.choices),self.orders))
        by_ref={value['ref']:value['object_id'] for value in self.request.subjects}
        return tuple(by_ref[ref] for actor,refs in self.request.groups for ref in selected.get(actor,refs))

    def to_dict(self):
        return {'schema_version':1,'request':self.request.to_dict(),'context':thaw_value(self.context),
            'resume_kind':self.resume_kind,'priority_player':self.priority_player,
            'held_triggers':[thaw_value(value) for value in self.held_triggers],
            'orders':[list(value) for value in self.orders]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','request','context','resume_kind',
                'priority_player','held_triggers','orders'} or type(value['schema_version']) is not int
                or value['schema_version']!=1 or any(not isinstance(value[key],list) for key in ('held_triggers','orders'))
                or any(not isinstance(order,list) for order in value['orders'])
                or any(not isinstance(item,Mapping) for item in value['held_triggers'])):
            raise TransformTimestampModelError('Transform timestamp continuation has a closed schema')
        return cls(TransformTimestampRequest.from_dict(value['request']),value['context'],value['resume_kind'],
            value['priority_player'],tuple(value['held_triggers']),tuple(tuple(order) for order in value['orders']))
