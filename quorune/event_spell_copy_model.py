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


NEXT_CAST_COPY_CAPABILITY = "trigger.delayed.next_cast_spell_copy"
NEXT_CAST_COPY_MECHANIC = "fixed-next-cast-spell-copy"
NEXT_CAST_COPY_FIELD = "next_cast_copy"


@dataclass(frozen=True, slots=True)
class NextCastCopySpec:
    scope: str
    maximum_mana_value: int | None = None
    copies: int = 1
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.scope) is not str or self.scope not in {"instant_or_sorcery", "lesson", "any"}
                or type(self.copies) is not int or self.copies not in (1, 2)
                or (self.maximum_mana_value is not None
                    and (type(self.maximum_mana_value) is not int or self.maximum_mana_value < 0))):
            raise ValueError("Next-cast copy requires a closed spell scope, mana ceiling and fixed copy count")

    def to_dict(self):
        return {"schema_version": self.schema_version, "scope": self.scope,
                "maximum_mana_value": self.maximum_mana_value, "copies": self.copies}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "scope", "maximum_mana_value", "copies"}:
            raise ValueError("Next-cast copy spec fields are closed")
        return cls(**dict(value))


def next_cast_copy_effect(spec: NextCastCopySpec):
    return {"op": "delayed_trigger", "controller": "$controller", NEXT_CAST_COPY_FIELD: spec.to_dict()}


def next_cast_copy_payload(effect, *, actor="$controller"):
    fields = {"op", "controller", NEXT_CAST_COPY_FIELD}
    if actor != "$controller":
        fields.add("_runtime_source")
    if not isinstance(effect, Mapping) or set(effect) != fields or effect["op"] != "delayed_trigger" or effect["controller"] != actor:
        raise ValueError("Next-cast copy effect requires a closed controller and source")
    return NextCastCopySpec.from_dict(effect[NEXT_CAST_COPY_FIELD])


def next_cast_copy_trigger_spec(trigger):
    if (trigger.event_kind != "spell.cast" or set(trigger.condition) != {NEXT_CAST_COPY_FIELD}
            or type(trigger.controller) is not str or not trigger.controller or type(trigger.active) is not bool
            or trigger.once is not True or type(trigger.created_turn_sequence) is not int
            or trigger.created_turn_sequence < 0 or type(trigger.expires_turn_sequence) is not int
            or trigger.expires_turn_sequence != trigger.created_turn_sequence or trigger.stack_template != {}
            or trigger.referred_object_ids
            or not isinstance(trigger.source_object_id, str) or not trigger.source_object_id
            or not isinstance(trigger.source_logical_object_id, str) or not trigger.source_logical_object_id):
        raise ValueError("Next-cast copy trigger requires its original source and same-turn one-shot window")
    return NextCastCopySpec.from_dict(trigger.condition[NEXT_CAST_COPY_FIELD])
