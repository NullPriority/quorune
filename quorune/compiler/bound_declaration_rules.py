from __future__ import annotations

"""Compile source-owned rules separately from characteristic and keyword grants."""

from ..ability_fragments import ability_fragment_to_dict
from ..bound_declaration_model import BoundStaticDeclarationRulesSpec,BOUND_DECLARATION_HANDLER
from ..object_predicate import ObjectQuerySpec


def bound_rule_handler(*,scope,query,rules,controller=None,exclude_source=False,condition=None):
    try:spec=BoundStaticDeclarationRulesSpec(scope,query,tuple(rules),controller,exclude_source,condition)
    except (TypeError,ValueError):return None
    return ('static-bound-declaration-rules-v1',{'handler_id':BOUND_DECLARATION_HANDLER,'schema_version':1,
        'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(spec)},spec.required_capabilities())


def bound_rule_for_target(target,rules,*,condition=None):
    scope=target['kind']
    query=(ObjectQuerySpec.from_dict(target['predicate']) if scope=='fixed_query'
        else ObjectQuerySpec(zones=('battlefield',),types_all=tuple(target['types_all'])))
    return bound_rule_handler(scope=scope,query=query,rules=rules,controller=target['target_controller'],
        exclude_source=target['exclude_source'],condition=condition)


def with_bound_rule_characteristics(compiled,rules,*,condition):
    """Retain the old characteristic payload while moving only rule ownership."""
    _template,descriptor,required=compiled
    rule=bound_rule_for_target(descriptor['target'],rules,condition=condition)
    if rule is None:return None
    modifier=dict(descriptor['modifier'])
    handlers=[]
    if modifier['add_abilities'] or modifier['power'] or modifier['toughness'] or modifier.get('add_ability_fragments'):
        from ..semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler,ConditionalTypedKeywordHandler
        handler=ConditionalTypedKeywordHandler() if modifier.get('add_ability_fragments') else FixedPublicStateCharacteristicsHandler()
        if handler.schema_version==1:modifier.pop('add_ability_fragments',None)
        characteristic={**descriptor,'handler_id':handler.handler_id,'schema_version':handler.schema_version,'modifier':modifier}
        try:handler.validate(characteristic)
        except (TypeError,ValueError):return None
        handlers.append(characteristic)
    handlers.append(rule[1])
    return 'static-conditional-characteristics-bound-rules-v1',tuple(handlers),tuple(sorted({*required,*rule[2]}))
