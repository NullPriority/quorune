from __future__ import annotations

"""Immutable acting-object facts for targeting and immediate source LKI."""

from collections.abc import Mapping
from dataclasses import dataclass
from .characteristic_evaluation import type_parts
from .errors import GameRuleError

TARGETING_SOURCE_CONTEXT='targeting_source_characteristics'


@dataclass(frozen=True,slots=True)
class TargetingSource:
    kind: str
    colors: frozenset[str] | None=None
    card_types: frozenset[str] | None=None
    subtypes: frozenset[str] | None=None
    source_controller: str | None=None

    def __post_init__(self):
        if self.kind not in {'spell','ability'}:raise ValueError('Targeting source kind must be explicit')
        if self.source_controller is not None and (type(self.source_controller) is not str or not self.source_controller):raise ValueError('Targeting source controller must be a seat or unavailable')
        for name in ('colors','card_types','subtypes'):
            value=getattr(self,name)
            if value is not None and (type(value) is not frozenset or any(type(v) is not str or not v for v in value)):
                raise ValueError('Targeting source characteristics must be immutable canonical terms')
        if self.colors is not None and not self.colors.issubset(set('WUBRG')):raise ValueError('Targeting colors must be canonical')
        if any(v!=v.casefold() for values in (self.card_types,self.subtypes) if values is not None for v in values):
            raise ValueError('Targeting types must be canonical')

    @classmethod
    def from_characteristics(cls,kind,value,*,controller=None):
        types,subtypes,_=type_parts(str(value.get('type_line') or ''))
        return cls(kind,frozenset(str(v).upper() for v in value.get('colors',())),frozenset(types),frozenset(subtypes),controller)


@dataclass(frozen=True,slots=True)
class TargetingSourceBinding:
    object_id: str
    object_ref: str
    logical_object_id: str
    zone: str
    controller: str
    type_line: str
    colors: tuple[str,...]

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.object_id,self.object_ref,self.logical_object_id,self.zone,self.controller,self.type_line)):
            raise ValueError('Targeting source binding requires its complete incarnation')
        if self.zone not in {'battlefield','stack','hand','graveyard','exile','command','library'}:raise ValueError('Unknown targeting source zone')
        if type(self.colors) is not tuple or len(set(self.colors))!=len(self.colors) or any(v not in tuple('WUBRG') for v in self.colors):
            raise ValueError('Targeting source binding has malformed colors')

    def to_dict(self):
        return {'schema_version':1,'object_id':self.object_id,'object_ref':self.object_ref,'logical_object_id':self.logical_object_id,
            'zone':self.zone,'controller':self.controller,'type_line':self.type_line,'colors':list(self.colors)}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','object_id','object_ref','logical_object_id','zone','controller','type_line','colors'}
                or type(value['schema_version']) is not int or value['schema_version']!=1 or type(value['colors']) not in (list,tuple)):
            raise ValueError('Targeting source binding fields are closed')
        return cls(value['object_id'],value['object_ref'],value['logical_object_id'],value['zone'],value['controller'],value['type_line'],tuple(value['colors']))

    def facts(self):return TargetingSource.from_characteristics('ability',{'type_line':self.type_line,'colors':self.colors},controller=self.controller)


def capture_targeting_source(host,source,*,characteristics=None,zone=None):
    data=host._effective_card_data(source) if characteristics is None else characteristics
    return TargetingSourceBinding(source.object_id,source.ref,source.logical_object_id,zone or source.zone,source.controller if (zone or source.zone) in {'battlefield','stack'} else source.owner,
        str(data.get('type_line') or ''),tuple(sorted(set(data.get('colors',())))))


def spell_targeting_source(host,card):
    return TargetingSource.from_characteristics('spell',host._effective_card_data(card),controller=card.controller)


def ability_targeting_source(host,card):
    return capture_targeting_source(host,card).facts()


def stack_targeting_source(host,item):
    if item.kind in {'spell','spell_copy'}:
        card=host.state.cards.get(item.card_object_id or item.source_object_id)
        if card is not None and card.zone=='stack':return spell_targeting_source(host,card)
        data=item.context.get('copy_permanent_characteristics')
        return TargetingSource.from_characteristics('spell',data) if isinstance(data,Mapping) else TargetingSource('spell')
    if item.kind not in {'activated_ability','triggered_ability'}:raise GameRuleError('Unsupported targeting stack object kind')
    source_id=item.source_object_id
    source=host.state.cards.get(source_id)
    raw=item.context.get(TARGETING_SOURCE_CONTEXT)
    try:binding=TargetingSourceBinding.from_dict(raw) if raw is not None else None
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    expected=item.context.get('source_logical_object_id')
    if binding is not None:
        if binding.object_id!=source_id or expected not in {None,binding.logical_object_id}:
            raise GameRuleError('Targeting source binding refers to another incarnation')
        if (source is not None and source.ref==binding.object_ref and source.logical_object_id==binding.logical_object_id
                and source.zone==binding.zone):return ability_targeting_source(host,source)
        return binding.facts()
    if source is not None and expected==source.logical_object_id:return ability_targeting_source(host,source)
    return TargetingSource('ability')


def pin_targeting_source_departures(host,cards,*,error_type=GameRuleError):
    """Use the canonical departure transaction for immediate source LKI."""
    from .trigger_batches import PendingTriggerBatch
    from .trigger_processing import replace_pending_trigger_batch
    try:
        departures={(c.object_id,c.logical_object_id):c for c in cards}
        updates={};pending=[]
        frames=[item.to_dict() for item in host.state.stack]
        for batch in host.state.pending_trigger_batches:
            frames.extend(item for group in batch.to_dict()['groups'] for item in group['items'])
        for item in frames:
            raw=item.get('context',{}).get(TARGETING_SOURCE_CONTEXT)
            if raw is None:continue
            binding=TargetingSourceBinding.from_dict(raw)
            if binding.object_id!=item.get('source_object_id'):raise ValueError('Targeting source frame identity changed')
            source=departures.get((binding.object_id,binding.logical_object_id))
            if source is not None and source.zone==binding.zone:
                updates[item['ref']]=capture_targeting_source(host,source).to_dict()
        for index,batch in enumerate(host.state.pending_trigger_batches):
            data=batch.to_dict();changed=False
            for group in data['groups']:
                for item in group['items']:
                    if item['ref'] in updates:item['context'][TARGETING_SOURCE_CONTEXT]=updates[item['ref']];changed=True
            if changed:pending.append((index,PendingTriggerBatch.from_dict(data)))
        for item in host.state.stack:
            if item.ref in updates:item.context[TARGETING_SOURCE_CONTEXT]=updates[item.ref]
        for index,batch in pending:replace_pending_trigger_batch(host,index,batch)
    except (ValueError,TypeError,KeyError) as exc:raise error_type(str(exc)) from exc


def ensure_trigger_targeting_source(host,item):
    if item.kind not in {'triggered_ability','activated_ability'} or TARGETING_SOURCE_CONTEXT in item.context:return
    program=host.semantics.get(item.semantic_key)
    from .ability_source_information import complete_ability_source_information
    if not (item.targets or item.context.get('target_schema_override') or getattr(program,'target_schema',None)
            or complete_ability_source_information(host.state)):return
    source=host.state.cards.get(item.source_object_id)
    if source is not None and item.context.get('source_logical_object_id',source.logical_object_id)==source.logical_object_id:
        item.context[TARGETING_SOURCE_CONTEXT]=capture_targeting_source(host,source).to_dict()


def spell_copy_targeting_source(host,template):
    card=host.state.cards.get(str(template.get('card_object_id') or ''))
    if card is not None:return spell_targeting_source(host,card)
    return TargetingSource('spell')
