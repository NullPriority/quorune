from __future__ import annotations

"""Typed static spell costs that inspect the announced target set."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from .replacement.immutable import FrozenMap,thaw_value

TARGETED_SPELL_COST_CAPABILITY='casting.cost.targeted_fixed_generic'
TARGETED_SPELL_COST_HANDLER='modification.cast-cost.targeted-fixed.v1'
TARGETED_SPELL_COST_EVENT='cast.cost.modify'
SELF_TARGET_REDUCTION_CAPABILITY='casting.cost.self_target_generic_reduction'
BATTLEFIELD_TARGET_REDUCTION_CAPABILITY='casting.cost.battlefield_target_generic_reduction'


class TargetedCostSubject(StrEnum):
    SOURCE='source'
    CONTROLLED_COMMANDER='controlled_commander'
    SPELL_TARGET='spell_target'


@dataclass(frozen=True,slots=True)
class TargetedSpellCostSpec:
    subject: TargetedCostSubject
    generic: int
    schema_version: int=1
    target_schema: FrozenMap | None=None
    caster_relation: str | None=None

    def __post_init__(self):
        if not isinstance(self.subject,TargetedCostSubject):raise ValueError('Target-dependent spell cost subject must be typed')
        if type(self.generic) is not int or type(self.schema_version) is not int:raise ValueError('Target-dependent cost requires exact integers')
        if self.schema_version == 3:
            if self.generic >= 0 or self.caster_relation not in {'any', 'source_controller', 'source_opponents'}:
                raise ValueError('Battlefield target reduction requires a negative fixed adjustment and typed caster relation')
            if self.subject is TargetedCostSubject.SOURCE:
                if self.target_schema is not None:
                    raise ValueError('Source target reduction cannot carry a different target predicate')
            elif self.subject is TargetedCostSubject.SPELL_TARGET:
                expected = {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1}
                if (self.caster_relation != 'source_controller' or not isinstance(self.target_schema, FrozenMap)
                        or type(self.target_schema.get('count')) is not int or thaw_value(self.target_schema) != expected):
                    raise ValueError('Global target reduction requires its closed current-creature predicate')
            else:
                raise ValueError('Battlefield target reduction has an unrepresented subject')
            return
        if self.caster_relation is not None:
            raise ValueError('Historical target cost schemas retain their implicit caster relation')
        if self.subject is TargetedCostSubject.SPELL_TARGET:
            if self.schema_version!=2 or self.generic>=0 or not isinstance(self.target_schema,FrozenMap):
                raise ValueError('Self target reduction requires its version-two sealed predicate and negative adjustment')
            schema=thaw_value(self.target_schema)
            allowed={'zones','categories','count','types_any','types_all','types_none','subtypes_any','subtypes_none',
                'supertypes_any','supertypes_none','keywords_all','keywords_none','colors_any','colors_none','colorless',
                'color_count_min','color_count_equal','mana_value','mana_value_min','mana_value_max','numeric_characteristic',
                'state_predicate','controller_relation','source_exclusion','token','commander','combat_state','damage_history'}
            domain=(schema.get('zones'),schema.get('categories'))
            if (set(schema)-allowed or domain not in ((['battlefield'],['permanent']),(['graveyard'],['card']),(['stack'],['spell']))
                    or type(schema.get('count')) is not int or schema['count']!=1):
                raise ValueError('Self target reduction requires one public permanent, graveyard card or spell predicate')
            # The rules-owned target authority validates the sealed leaf fields
            # at component admission; this domain value owns its outer contract.
        elif self.schema_version!=1 or self.target_schema is not None or self.generic<1:
            raise ValueError('Historical target-dependent cost requires a positive fixed generic increase')

    def to_dict(self):
        return {'schema_version':self.schema_version,'subject':self.subject.value,'generic':self.generic,
            **({'target_schema':thaw_value(self.target_schema)} if self.schema_version in {2, 3} else {}),
            **({'caster_relation':self.caster_relation} if self.schema_version==3 else {})}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping):raise ValueError('Target-dependent spell cost requires an object')
        version=value.get('schema_version');expected={'schema_version','subject','generic'}|({'target_schema'} if version in {2,3} else set())|({'caster_relation'} if version==3 else set())
        if set(value)!=expected or type(version) is not int or version not in (1,2,3):raise ValueError('Target-dependent spell cost fields are closed')
        if version==2 and not isinstance(value['target_schema'],Mapping):raise ValueError('Self target reduction predicate requires an object')
        if version==3 and value['target_schema'] is not None and not isinstance(value['target_schema'],Mapping):raise ValueError('Battlefield target predicate requires a closed object or null')
        return cls(TargetedCostSubject(value['subject']),value['generic'],version,
            FrozenMap(value['target_schema']) if version in {2,3} and value['target_schema'] is not None else None,
            value['caster_relation'] if version==3 else None)


@dataclass(frozen=True,slots=True)
class TargetedSpellCostSource:
    source_ref: str
    source_identity: str
    controller: str
    component_id: str
    spec: TargetedSpellCostSpec
    eligible_target_refs: tuple[str,...]

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.source_ref,self.source_identity,self.controller,self.component_id))
                or not isinstance(self.spec,TargetedSpellCostSpec) or type(self.eligible_target_refs) is not tuple
                or any(type(ref) is not str or not ref for ref in self.eligible_target_refs)
                or len(set(self.eligible_target_refs))!=len(self.eligible_target_refs)):
            raise ValueError('Target-dependent cost source requires captured public identities')

    def applies(self,targets):return bool(set(targets).intersection(self.eligible_target_refs))


def validate_targeted_spell_cost_fact(value):
    if not isinstance(value,Mapping):raise ValueError('Target-dependent spell cost fact requires an object')
    version=value.get('schema_version');expected={'schema_version','generic','sources'}|({'generic_reduction'} if version==2 else set())
    if (set(value)!=expected or type(version) is not int or version not in (1,2)
            or type(value['generic']) is not int or value['generic']<0 or not isinstance(value['sources'],(list,tuple))):
        raise ValueError('Target-dependent spell cost fact fields are closed')
    if version==2 and (type(value['generic_reduction']) is not int or value['generic_reduction']<0):raise ValueError('Target-dependent reduction must be nonnegative')
    components=set();total=0;reduction=0
    for source in value['sources']:
        if (not isinstance(source,Mapping) or set(source)!={'component_id','source_ref','source_identity','controller','generic'}
                or any(type(source[key]) is not str or not source[key] for key in ('component_id','source_ref','source_identity','controller'))
                or type(source['generic']) is not int or (source['generic']<1 if version==1 else source['generic']==0) or source['component_id'] in components):
            raise ValueError('Target-dependent spell cost repeats or loses its captured source')
        components.add(source['component_id']);total+=max(0,source['generic']);reduction+=max(0,-source['generic'])
    if value['generic']!=total or version==2 and value['generic_reduction']!=reduction:raise ValueError('Target-dependent spell cost differs from its source total')
    return total


def self_target_reduction_runtime_dependencies(program):
    required=()
    for descriptor in program.handlers:
        if descriptor.get('handler_id')!=TARGETED_SPELL_COST_HANDLER:continue
        spec=TargetedSpellCostSpec.from_dict(descriptor.get('modifier'))
        if spec.schema_version==3:
            if program.active_zone!='battlefield' or program.event!=TARGETED_SPELL_COST_EVENT:
                raise ValueError('Battlefield target reduction requires its current source activity contract')
            required=tuple(sorted({*required,BATTLEFIELD_TARGET_REDUCTION_CAPABILITY,TARGETED_SPELL_COST_CAPABILITY}))
            continue
        if spec.subject is not TargetedCostSubject.SPELL_TARGET:continue
        if program.active_zone!='all' or program.event!=TARGETED_SPELL_COST_EVENT or 'self_cast_cost_reduction' not in program.coverage:
            raise ValueError('Self target reduction requires its source-face cost contract')
        required=tuple(sorted({*required,SELF_TARGET_REDUCTION_CAPABILITY,TARGETED_SPELL_COST_CAPABILITY}))
    return required
