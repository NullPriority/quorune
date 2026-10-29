from __future__ import annotations

"""Giver-owned declaration rules with immutable current receiver bindings."""

from dataclasses import dataclass
from typing import Mapping
from .object_predicate import ObjectQuerySpec,object_query_capabilities
from .object_query_union import validate_receiver_union_template
from .continuous_conditions import FixedPublicStateConditionSpec
from .declaration_fragments import DeclarationRestrictionTemplate,DeclarationRequirementTemplate,DECLARATION_COMPONENT_CAPABILITY_ID

BOUND_DECLARATION_CAPABILITY='combat.declaration.bound_static_rules'
BOUND_DECLARATION_HANDLER='ability.static.bound-declaration-rules.v1'


@dataclass(frozen=True,slots=True)
class BoundStaticDeclarationRulesSpec:
    scope:str
    query:ObjectQuerySpec
    rules:tuple[DeclarationRestrictionTemplate|DeclarationRequirementTemplate,...]
    controller:str|None=None
    exclude_source:bool=False
    source_condition:FixedPublicStateConditionSpec|None=None
    schema_version:int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1 or type(self.scope) is not str
                or self.scope not in {'source','attached','fixed_query'} or type(self.exclude_source) is not bool
                or not isinstance(self.query,ObjectQuerySpec) or type(self.rules) is not tuple or not 1<=len(self.rules)<=4):
            raise ValueError('Bound declaration rules have closed identity, scope and children')
        validate_receiver_union_template(self.query)
        def validate_query(query):
            if (query.zones not in {(),('battlefield',)} or query.owner is not None or query.controller is not None
                    or query.excluded_controllers or query.exclude_ref is not None or query.known_to_actor is not None or query.include_phased_out):
                raise ValueError('Declaration receiver queries reserve authority, visibility and current presence')
            for child in query.any_of:validate_query(child)
        validate_query(self.query)
        if self.scope=='fixed_query':
            if type(self.controller) is not str or self.controller not in {'any','source_controller','source_opponents','source_owner'}:
                raise ValueError('Queried declarations require a closed controller relation')
            if (self.controller=='source_owner' or self.query.commander is not None) and (
                    self.query.commander is not True or self.query.types_all!=('creature',)
                    or self.controller not in {'source_controller','source_owner'}):
                raise ValueError('Owner queries require designated commander creatures')
        elif self.controller is not None or self.exclude_source:
            raise ValueError('Source and attached rules cannot widen their identity binding')
        if self.source_condition is not None and not isinstance(self.source_condition,FixedPublicStateConditionSpec):
            raise ValueError('Declaration giver conditions must be typed')
        for rule in self.rules:
            if isinstance(rule,DeclarationRequirementTemplate):
                if rule.scope!='self' or rule.condition is not None:raise ValueError('Bound intrinsic requirements reserve inner source conditions')
            elif isinstance(rule,DeclarationRestrictionTemplate):
                if rule.scope not in {'self','source_option'}:raise ValueError('Bound restrictions require an intrinsic receiver anchor')
                def has_controller_reference(value):
                    if isinstance(value,Mapping):
                        return any(k in {'player','compare_player'} and v=='source_controller' or has_controller_reference(v) for k,v in value.items())
                    if isinstance(value,list):return any(has_controller_reference(v) for v in value)
                    return False
                if (rule.option_relation=='source_controller' or rule.condition is not None and has_controller_reference(rule.condition.to_dict())) and (
                        self.scope!='source' and self.controller!='source_controller'):
                    raise ValueError('Receiver-controller rule references require the same giver controller')
            else:raise ValueError('Bound declaration children must be typed rules')

    @property
    def capability_id(self):return BOUND_DECLARATION_CAPABILITY

    def required_capabilities(self):
        result={BOUND_DECLARATION_CAPABILITY,DECLARATION_COMPONENT_CAPABILITY_ID,*object_query_capabilities(self.query)}
        if self.query.commander is not None:result.add('state_query.commander.designation')
        if self.source_condition is not None:result.add('continuous.characteristics.fixed_public_state')
        if self.source_condition is not None:
            for leaf in self.source_condition.leaf_conditions():
                if leaf.kind.value=='controller_has_citys_blessing':result.add('player.designation.citys_blessing')
                if leaf.fact is not None and leaf.fact.value=='source_renowned':result.add('permanent.designation.renowned')
        if any(isinstance(r,DeclarationRestrictionTemplate) and r.condition is not None for r in self.rules):
            result.add('combat.declaration.public_state_conditions')
        return tuple(sorted(result))

    def to_dict(self):
        return {'schema_version':1,'scope':self.scope,'query':self.query.to_dict(),'controller':self.controller,
            'exclude_source':self.exclude_source,'source_condition':self.source_condition.to_dict() if self.source_condition is not None else None,
            'rules':[{'kind':'declaration_requirement' if isinstance(r,DeclarationRequirementTemplate) else 'declaration_restriction','value':r.to_dict()} for r in self.rules]}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','scope','query','controller','exclude_source','source_condition','rules'} or not isinstance(value['rules'],list):
            raise ValueError('Bound declaration rule fields are closed')
        rules=[]
        for child in value['rules']:
            if not isinstance(child,Mapping) or set(child)!={'kind','value'} or child['kind'] not in {'declaration_requirement','declaration_restriction'}:
                raise ValueError('Bound declaration child fields are closed')
            kind=DeclarationRequirementTemplate if child['kind']=='declaration_requirement' else DeclarationRestrictionTemplate
            rules.append(kind.from_dict(child['value']))
        return cls(value['scope'],ObjectQuerySpec.from_dict(value['query']),tuple(rules),value['controller'],value['exclude_source'],
            FixedPublicStateConditionSpec.from_dict(value['source_condition']) if value['source_condition'] is not None else None,value['schema_version'])
