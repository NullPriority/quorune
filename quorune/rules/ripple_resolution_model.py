"""Closed Ripple reveal-set and resolving-cast values over canonical identities."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..replacement.immutable import FrozenMap,thaw_value
from ..ripple_model import RippleSpec,RippleCastSnapshot
from .library_selection import LibrarySelectionObjectIdentity
from .ripple import RIPPLE_FIELD,decode_ripple_instruction,ripple_instruction

RIPPLE_STATE_FIELD='ripple_state'
_BASE_FIELDS={'op','player',RIPPLE_FIELD,'cast_snapshot'}


@dataclass(frozen=True,slots=True)
class RippleResolutionState:
    spec: RippleSpec
    source: RippleCastSnapshot
    stack_ref: str
    looked: tuple[LibrarySelectionObjectIdentity,...]
    stage: str='cast'
    schema_version: int=1
    actor: str | None=None

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.spec,RippleSpec) or not isinstance(self.source,RippleCastSnapshot)
                or type(self.stack_ref) is not str or not self.stack_ref or self.stage not in ('cast','bottom')
                or not isinstance(self.looked,tuple) or len(self.looked)>self.spec.count
                or any(not isinstance(i,LibrarySelectionObjectIdentity) for i in self.looked)
                or any(len({getattr(i,k) for i in self.looked})!=len(self.looked)
                    for k in ('object_id','logical_object_id','ref'))):
            raise ValueError('Ripple resolution requires its exact original revealed identities and stage')
        if self.actor is None:object.__setattr__(self,'actor',self.source.controller)
        if type(self.actor) is not str or not self.actor:raise ValueError('Ripple requires its current resolving controller')

    def effect(self):
        return {**ripple_instruction(self.spec),'player':self.actor,'cast_snapshot':self.source.to_dict(),
            RIPPLE_STATE_FIELD:{'schema_version':1,'stack_ref':self.stack_ref,'stage':self.stage,
                'looked':[i.to_dict() for i in self.looked]}}

    @classmethod
    def from_effect(cls,effect,*,actor):
        if not isinstance(effect,Mapping) or set(effect)!=_BASE_FIELDS|{RIPPLE_STATE_FIELD}:
            raise ValueError('Ripple resolution fields are closed')
        spec,source=decode_ripple_instruction({k:effect[k] for k in _BASE_FIELDS},actor=actor,allow_symbolic=False)
        state=effect[RIPPLE_STATE_FIELD]
        if (not isinstance(state,Mapping) or set(state)!={'schema_version','stack_ref','stage','looked'}
                or not isinstance(state['looked'],(list,tuple))):
            raise ValueError('Ripple revealed state fields are closed')
        return cls(spec,source,state['stack_ref'],tuple(LibrarySelectionObjectIdentity.from_dict(i) for i in state['looked']),
            stage=state['stage'],schema_version=state['schema_version'],actor=actor)

    def remaining(self,query):
        result=[]
        for identity in self.looked:
            row=query.object(identity.ref,zones=('library',))
            if row is None:continue
            if row.object_id!=identity.object_id or row.logical_object_id!=identity.logical_object_id:continue
            if row.owner!=self.actor:raise ValueError('A revealed Ripple card changed library owner')
            result.append(identity)
        return tuple(result)


@dataclass(frozen=True,slots=True)
class ResolveRippleCastIntent:
    actor: str
    stack_ref: str
    state: RippleResolutionState
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple=()

    def __post_init__(self):
        if (not isinstance(self.state,RippleResolutionState) or self.state.stage!='cast'
                or self.actor!=self.state.actor or self.stack_ref!=self.state.stack_ref
                or type(self.options_fingerprint) is not str or len(self.options_fingerprint)!=64
                or any(c not in '0123456789abcdef' for c in self.options_fingerprint)
                or not isinstance(self.response,FrozenMap) or not isinstance(self.replacement_selections,tuple)):
            raise ValueError('Ripple cast intent requires its resolving controller, reveal set and current price')
        if any(type(k) is not str or k.startswith('_') for k in self.response):
            raise ValueError('Internal Ripple casting payment fields cannot be supplied')
        from ..semantic_runtime.zone_move_intent import _freeze_replacement_selections
        object.__setattr__(self,'replacement_selections',_freeze_replacement_selections(
            self.replacement_selections,family='Ripple casting'))

    def identity(self):
        return {'actor':self.actor,'stack_ref':self.stack_ref,'state':self.state.effect(),
            'options_fingerprint':self.options_fingerprint,'response':thaw_value(self.response)}


@dataclass(frozen=True,slots=True)
class RippleBottomIntent:
    actor: str
    stack_ref: str
    state: RippleResolutionState
    order: tuple[str,...]

    def __post_init__(self):
        if (not isinstance(self.state,RippleResolutionState) or self.state.stage!='bottom'
                or self.actor!=self.state.actor or self.stack_ref!=self.state.stack_ref
                or not isinstance(self.order,tuple) or any(type(ref) is not str or not ref for ref in self.order)
                or len(set(self.order))!=len(self.order)
                or set(self.order)!={i.ref for i in self.state.looked}):
            raise ValueError('Ripple bottom intent requires exactly its remaining revealed cards in chosen order')

    def identity(self):
        return {'actor':self.actor,'stack_ref':self.stack_ref,'state':self.state.effect(),'order':list(self.order)}


def validate_ripple_cast_identity(value):
    if (not isinstance(value,Mapping) or set(value)!={'actor','stack_ref','state','options_fingerprint','response'}
            or not isinstance(value['response'],Mapping)):
        raise ValueError('Ripple casting continuation identity is closed')
    state=RippleResolutionState.from_effect(value['state'],actor=value['actor'])
    return ResolveRippleCastIntent(value['actor'],value['stack_ref'],state,value['options_fingerprint'],FrozenMap(value['response'])).identity()


def validate_ripple_bottom_identity(value):
    if not isinstance(value,Mapping) or set(value)!={'actor','stack_ref','state','order'} or not isinstance(value['order'],(list,tuple)):
        raise ValueError('Ripple bottom continuation identity is closed')
    state=RippleResolutionState.from_effect(value['state'],actor=value['actor'])
    return RippleBottomIntent(value['actor'],value['stack_ref'],state,tuple(value['order'])).identity()
