from __future__ import annotations

"""Pure staged decisions inside the application of one Tribute replacement."""

from collections.abc import Mapping
from dataclasses import dataclass,replace

from ..tribute_model import TributeSpec,TRIBUTE_ENTRY_EVENT
from .immutable import thaw_value


@dataclass(frozen=True,slots=True)
class OpenTributeEntryChoice:
    spec: TributeSpec
    opponent: str
    players: tuple[str,...]
    choice_id: str

    def __post_init__(self):
        if (not isinstance(self.spec,TributeSpec) or not isinstance(self.players,tuple)
                or any(type(p) is not str or not p for p in self.players)
                or len(set(self.players))!=len(self.players) or self.opponent not in self.players
                or type(self.choice_id) is not str or not self.choice_id):
            raise ValueError('Tribute requires a captured opponent among distinct active players')

    def to_dict(self):
        return {'op':'open_tribute_entry_choice','spec':self.spec.to_dict(),
            'opponent':self.opponent,'players':list(self.players),'choice_id':self.choice_id}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'op','spec','opponent','players','choice_id'}
                or value['op']!='open_tribute_entry_choice' or not isinstance(value['players'],(list,tuple))):
            raise ValueError('Tribute entry opening fields are closed')
        return cls(TributeSpec.from_dict(value['spec']),value['opponent'],tuple(value['players']),value['choice_id'])


@dataclass(frozen=True,slots=True)
class CompleteTributeEntryChoice:
    spec: TributeSpec
    accepted: bool

    def __post_init__(self):
        if not isinstance(self.spec,TributeSpec) or type(self.accepted) is not bool:
            raise ValueError('Tribute completion requires a typed specification and decision')

    def to_dict(self):
        return {'op':'complete_tribute_entry_choice','spec':self.spec.to_dict(),'accepted':self.accepted}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'op','spec','accepted'}
                or value['op']!='complete_tribute_entry_choice'):
            raise ValueError('Tribute entry completion fields are closed')
        return cls(TributeSpec.from_dict(value['spec']),value['accepted'])


def open_tribute_entry_choice(event,payload,children,operation,*,effect_id):
    from .model import ReplaceableEvent,ReplacementEffectError
    controller=payload.get('destination_controller')
    if (event.kind!='zone.change' or event.affected_object is None
            or payload.get('destination')!='battlefield' or controller not in operation.players
            or operation.opponent==controller or event.chooser!=controller or operation.choice_id!=effect_id):
        raise ReplacementEffectError('Tribute belongs to its entering controller and a captured opponent')
    child=ReplaceableEvent(event_id=f'{event.event_id}/tribute:{effect_id}',kind=TRIBUTE_ENTRY_EVENT,
        affected_player=operation.opponent,entry_scope=event.entry_scope,
        payload={'schema_version':1,'spec':operation.spec.to_dict(),'entry_id':event.event_id,'replacement_id':effect_id,
            'object_ref':payload.get('object_ref'),
            'controller':controller,'opponent':operation.opponent,'players':list(operation.players),
            'subject':event.affected_object.to_dict(),'zone':thaw_value(payload),'accepted':None})
    children.append(child)


def validate_tribute_choice_event(event,*,pending=False):
    from .model import AffectedObject,ReplacementEffectError
    p=event.payload
    expected={'schema_version','spec','entry_id','replacement_id','object_ref','controller','opponent','players','subject','zone','accepted'}
    if (event.kind!=TRIBUTE_ENTRY_EVENT or set(p)-{'replacement_choosers'}!=expected or type(p['schema_version']) is not int
            or p['schema_version']!=1 or event.affected_object is not None
            or event.affected_player!=p['opponent'] or type(p['entry_id']) is not str or not p['entry_id']
            or type(p['replacement_id']) is not str or not p['replacement_id']
            or event.event_id!=f"{p['entry_id']}/tribute:{p['replacement_id']}"):
        raise ReplacementEffectError('Tribute child decision identity or fields changed')
    spec=TributeSpec.from_dict(p['spec']);players=p['players'];zone=p['zone']
    if (not isinstance(players,(list,tuple)) or any(type(v) is not str or not v for v in players)
            or len(set(players))!=len(players) or p['controller'] not in players or p['opponent'] not in players
            or p['controller']==p['opponent'] or not isinstance(zone,Mapping)
            or zone.get('destination')!='battlefield' or zone.get('destination_controller')!=p['controller']
            or type(zone.get('object_ref')) is not str or not zone['object_ref'] or p['object_ref']!=zone['object_ref']):
        raise ReplacementEffectError('Tribute child lost its captured entry or opponent')
    subject=AffectedObject.from_dict(p['subject'])
    if subject.chooser!=p['controller']:
        raise ReplacementEffectError('Tribute affected object controller changed')
    if pending and (p['accepted'] is not None or event.children):
        raise ReplacementEffectError('Tribute decision is already complete')
    if p['accepted'] is not None and type(p['accepted']) is not bool:
        raise ReplacementEffectError('Tribute decision must be an explicit boolean')
    history=p.get('replacement_choosers',{})
    expected_history={} if p['accepted'] is None else {p['replacement_id']+':decision':p['opponent']}
    if not isinstance(history,Mapping) or thaw_value(history)!=expected_history or set(event.applied_effects)!=set(expected_history):
        raise ReplacementEffectError('Tribute decision journal or chooser history changed')
    return spec,subject


def complete_tribute_entry_choice(event,payload,children,operation,*,effect_id):
    from .model import ReplaceableEvent,ReplacementEffectError
    from .operations import CreateAffectedObjectCounter
    from .application import _create_affected_object_counter_event
    spec,subject=validate_tribute_choice_event(event,pending=True)
    if spec!=operation.spec:
        raise ReplacementEffectError('Tribute decision specification changed')
    payload['accepted']=operation.accepted
    if not operation.accepted:return
    # Reuse the captured containing zone subject to construct its counter
    # request. The adapter itself is not an additional zone-change event.
    entry=ReplaceableEvent(event_id=payload['entry_id'],kind='zone.change',affected_player=None,
        affected_object=subject,payload=payload['zone'],entry_scope=event.entry_scope)
    counter=CreateAffectedObjectCounter(counter_name='+1/+1',amount=spec.amount,
        placing_player=event.affected_player,source_ref=payload['zone']['object_ref'],sequence=0)
    _create_affected_object_counter_event(entry,payload['zone'],children,counter,effect_id=effect_id)
    children[-1]=replace(children[-1],event_id=f'{event.event_id}/counter:0')


def pending_tribute_entry_choice(event):
    return event.kind==TRIBUTE_ENTRY_EVENT and event.payload.get('accepted') is None


def validate_tribute_replacement(effect,operations,decline):
    from .model import ReplacementClass,ReplacementEffectError
    if not any(isinstance(o,(OpenTributeEntryChoice,CompleteTributeEntryChoice)) for o in (*operations,*decline)):
        return
    if len(operations)!=1 or effect.replacement_class is not ReplacementClass.OTHER:
        raise ReplacementEffectError('Tribute uses one closed ordinary entry decision')
    op=operations[0]
    if isinstance(op,OpenTributeEntryChoice):
        expected={'object_ref':{'eq':effect.source_id},'destination':{'eq':'battlefield'},
            'destination_controller':{'not_in':[op.opponent]}}
        if (effect.event_kind!='zone.change' or effect.optional or decline or not effect.occurrence_id
                or effect.effect_id!=f'{effect.occurrence_id}:opponent:{op.opponent}'
                or op.choice_id!=effect.effect_id or thaw_value(effect.conditions)!=expected):
            raise ReplacementEffectError('Tribute opponent variants require one bound shared entry occurrence')
        return
    if not isinstance(op,CompleteTributeEntryChoice):
        raise ReplacementEffectError('Tribute decisions cannot be attached to unrelated operations')
    c=thaw_value(effect.conditions)
    root=c.get('replacement_id',{});opponent=c.get('opponent',{})
    root=root.get('eq') if isinstance(root,Mapping) else None
    opponent=opponent.get('eq') if isinstance(opponent,Mapping) else None
    expected={'replacement_id':{'eq':root},'object_ref':{'eq':effect.source_id},
        'opponent':{'eq':opponent},'spec':{'eq':op.spec.to_dict()},'accepted':{'eq':None}}
    if (not op.accepted or len(decline)!=1
            or not isinstance(decline[0],CompleteTributeEntryChoice) or decline[0].accepted
            or decline[0].spec!=op.spec or not effect.optional or effect.event_kind!=TRIBUTE_ENTRY_EVENT
            or type(root) is not str or not root or type(opponent) is not str or not opponent
            or effect.effect_id!=root+':decision' or c!=expected or effect.occurrence_id is not None):
        raise ReplacementEffectError('Tribute counter decisions require their bound opponent and complementary outcomes')


def tribute_entry_replacements(spec,*,object_ref,component_id,players):
    from .model import ReplacementClass,ReplacementEffect
    result=[];occurrence=f'tribute:{object_ref}:{component_id}'
    for opponent in players:
        root_id=f'{occurrence}:opponent:{opponent}'
        result.append(ReplacementEffect(effect_id=root_id,source_id=object_ref,event_kind='zone.change',
            replacement_class=ReplacementClass.OTHER,occurrence_id=occurrence,
            conditions={'object_ref':{'eq':object_ref},'destination':{'eq':'battlefield'},
                'destination_controller':{'not_in':[opponent]}},
            operations=(OpenTributeEntryChoice(spec,opponent,tuple(players),root_id),),
            label=f'{object_ref}: choose {opponent} to decide Tribute {spec.amount}'))
        result.append(ReplacementEffect(effect_id=f'{root_id}:decision',source_id=object_ref,
            event_kind=TRIBUTE_ENTRY_EVENT,replacement_class=ReplacementClass.OTHER,
            conditions={'replacement_id':{'eq':root_id},'object_ref':{'eq':object_ref},'opponent':{'eq':opponent},'spec':{'eq':spec.to_dict()},'accepted':{'eq':None}},
            operations=(CompleteTributeEntryChoice(spec,True),),
            decline_operations=(CompleteTributeEntryChoice(spec,False),),optional=True,
            label=f'Put {spec.amount} +1/+1 counters on {object_ref}',decline_label='Do not pay Tribute'))
    return tuple(result)
