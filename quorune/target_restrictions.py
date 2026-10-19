from __future__ import annotations

"""Closed targeting prohibitions, independent from Protection and Hexproof."""

from collections.abc import Mapping
from dataclasses import dataclass
from .target_source import TargetingSource

TARGET_RESTRICTION_CAPABILITY='target.prohibition.fixed_source_quality'
TARGET_RESTRICTION_HANDLER='ability.static.target-prohibition.v1'


@dataclass(frozen=True,slots=True)
class TargetRestrictionSpec:
    affected: str
    kinds: tuple[str,...]
    controller_relation: str='any'
    colors_any: tuple[str,...]=()
    colors_none: tuple[str,...]=()
    types_all: tuple[str,...]=()
    subtypes_all: tuple[str,...]=()

    def __post_init__(self):
        if type(self.affected) is not str or self.affected not in {'source','attached_creature','creatures','controlled_creatures','graveyard_cards'}:
            raise ValueError('Target prohibition affected set is closed')
        if type(self.kinds) is not tuple or not self.kinds or any(type(v) is not str or v not in {'spell','ability'} for v in self.kinds):
            raise ValueError('Target prohibition needs explicit spell/ability kinds')
        if type(self.controller_relation) is not str or self.controller_relation not in {'any','acting_opponent','source_opponent'}:
            raise ValueError('Target prohibition controller relation is closed')
        if self.controller_relation=='source_opponent' and self.kinds!=('ability',):raise ValueError('Source control is an ability-source predicate')
        for field in ('kinds','colors_any','colors_none','types_all','subtypes_all'):
            value=getattr(self,field)
            if type(value) is not tuple or any(type(v) is not str for v in value) or value!=tuple(sorted(set(value))):
                raise ValueError('Target prohibition predicates must be canonical immutable terms')
        if any(v not in 'WUBRG' or len(v)!=1 for v in (*self.colors_any,*self.colors_none)):
            raise ValueError('Target prohibition colors must be canonical')
        if set(self.colors_any)&set(self.colors_none):raise ValueError('Target prohibition colors conflict')
        if set(self.types_all)-{'artifact'} or set(self.subtypes_all)-{'aura'}:raise ValueError('Target prohibition type predicates are closed')

    def to_dict(self):
        return {'schema_version':1,'affected':self.affected,'kinds':list(self.kinds),'controller_relation':self.controller_relation,
            'colors_any':list(self.colors_any),'colors_none':list(self.colors_none),'types_all':list(self.types_all),'subtypes_all':list(self.subtypes_all)}

    @classmethod
    def from_dict(cls,value):
        fields={'schema_version','affected','kinds','controller_relation','colors_any','colors_none','types_all','subtypes_all'}
        if (not isinstance(value,Mapping) or set(value)!=fields or type(value['schema_version']) is not int or value['schema_version']!=1
                or any(type(value[k]) not in (list,tuple) for k in ('kinds','colors_any','colors_none','types_all','subtypes_all'))):
            raise ValueError('Target prohibition fields are closed')
        return cls(value['affected'],tuple(value['kinds']),value['controller_relation'],
            *(tuple(value[k]) for k in ('colors_any','colors_none','types_all','subtypes_all')))

    def blocks(self,facts,*,acting_controller,observer_controller):
        if facts is None:return True
        if not isinstance(facts,TargetingSource):raise ValueError('Target prohibition requires typed acting facts')
        if facts.kind not in self.kinds:return False
        if self.controller_relation=='acting_opponent' and acting_controller==observer_controller:return False
        if self.controller_relation=='source_opponent' and facts.source_controller==observer_controller:return False
        if self.colors_any and facts.colors is not None and not set(self.colors_any)&facts.colors:return False
        if self.colors_none and facts.colors is not None and set(self.colors_none)&facts.colors:return False
        if self.types_all and facts.card_types is not None and not set(self.types_all).issubset(facts.card_types):return False
        if self.subtypes_all and facts.subtypes is not None and not set(self.subtypes_all).issubset(facts.subtypes):return False
        # Missing source information cannot prove this potential prohibition absent.
        return True


@dataclass(frozen=True,slots=True)
class TargetProhibitionSnapshot:
    specification: TargetRestrictionSpec
    object_id: str
    controller: str
    attached_to: str | None


def current_target_prohibitions(host):
    """Capture one query's effective static inputs, never a cross-query cache."""
    from .ability_fragments import StaticComponentSpec
    from .semantic_runtime.target_restrictions import TargetRestrictionHandler
    handler=TargetRestrictionHandler();values=[]
    for source in tuple(host.state.cards.values()):
        if source.zone!='battlefield' or source.phased_out or source.controller not in host.active_seats:continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment,StaticComponentSpec):continue
            program=host.semantics.get(fragment.semantic_key)
            if program is None or not host.semantic_program_is_current_trusted(program):continue
            for descriptor in program.handlers:
                if descriptor.get('handler_id')!=TARGET_RESTRICTION_HANDLER:continue
                values.extend(TargetProhibitionSnapshot(spec,source.object_id,source.controller,source.attached_to)
                    for spec in handler.validate(descriptor))
    return tuple(values)


def current_target_prohibition(host,*,row,acting_controller,acting_source,prohibitions=None):
    from .model import CardInstance
    target=row.get('card')
    if not isinstance(target,CardInstance):return False
    snapshots=current_target_prohibitions(host) if prohibitions is None else prohibitions
    return any(_affected_target_matches(host,value.specification,value,target,row) and value.specification.blocks(
        acting_source,acting_controller=acting_controller,observer_controller=value.controller) for value in snapshots)


def _affected_target_matches(host,spec,source,target,row):
    if spec.affected=='graveyard_cards':return row.get('zone')=='graveyard' and row.get('category')=='card' and target.is_card_object
    if row.get('zone')!='battlefield':return False
    if spec.affected=='source':return target.object_id==source.object_id
    if spec.affected=='attached_creature' and target.object_id!=source.attached_to:return False
    data=host._effective_card_data(target)
    from .characteristic_evaluation import type_parts
    if 'creature' not in type_parts(str(data.get('type_line') or ''))[0]:return False
    return spec.affected!='controlled_creatures' or target.controller==source.controller
