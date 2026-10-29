"""Immutable information for an exact spell referenced by its cast trigger."""

from collections.abc import Mapping
from dataclasses import dataclass

from .model import StackItem
from .replacement.immutable import FrozenMap,thaw_value

EVENT_SPELL_COPY_CAPABILITY='stack.copy.cast_event_spell'
EVENT_SPELL_COPY_MECHANIC='cast-event-spell-copy'
EVENT_SPELL_COPY_CONTEXT='event_spell_copy_source'


@dataclass(frozen=True,slots=True)
class EventSpellCopySource:
    stack_item:FrozenMap
    characteristics:FrozenMap
    target_schema:FrozenMap|None
    logical_object_id:str
    departed:bool=False
    schema_version:int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or type(self.departed) is not bool:
            raise ValueError('Event spell-copy source has one closed version and departure mode')
        for name in ('stack_item','characteristics'):
            if not isinstance(getattr(self,name),FrozenMap):object.__setattr__(self,name,FrozenMap(getattr(self,name)))
        if self.target_schema is not None and not isinstance(self.target_schema,FrozenMap):object.__setattr__(self,'target_schema',FrozenMap(self.target_schema))
        item=self.item()
        if item.kind not in {'spell','spell_copy'} or any(type(v) is not str or not v for v in (item.ref,item.stack_id,item.card_object_id,self.logical_object_id)):
            raise ValueError('Event spell-copy source requires the exact represented spell incarnation')
        if item.semantic_key is not None and (type(item.semantic_key) is not str or not item.semantic_key):raise ValueError('Copied resolution identity must be represented or an empty core spell')
        if type(self.characteristics.get('type_line')) is not str or not self.characteristics['type_line']:
            raise ValueError('Event spell-copy source requires its copiable characteristics')
        if item.x_value is not None and (type(item.x_value) is not int or item.x_value<0):raise ValueError('Copied X must be a nonnegative choice')
        if any(type(v) is not str for v in item.targets) or any(type(v) is not str for v in item.modes):raise ValueError('Copied target and mode choices are closed strings')

    def item(self):return StackItem.from_dict(thaw_value(self.stack_item))

    def to_dict(self):
        return {'schema_version':1,'stack_item':thaw_value(self.stack_item),'characteristics':thaw_value(self.characteristics),
            'target_schema':thaw_value(self.target_schema) if self.target_schema is not None else None,'logical_object_id':self.logical_object_id,'departed':self.departed}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','stack_item','characteristics','target_schema','logical_object_id','departed'}:
            raise ValueError('Event spell-copy source fields are closed')
        return cls(**dict(value))


def event_spell_copy_effect(*,retain_targets_only=False):
    return {'op':'copy_stack_item','stack':'$context.stack','copy_source':'$context.'+EVENT_SPELL_COPY_CONTEXT,
        **({'retain_targets_only':True} if retain_targets_only else {})}
