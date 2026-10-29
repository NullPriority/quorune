from __future__ import annotations

"""Whole-clause source binding for static all-able-blocker requirements."""

import re

from ..ability_fragments import ability_fragment_to_dict
from ..declaration_fragments import (
    DeclarationRequirementTemplate, DECLARATION_COMPONENT_CAPABILITY_ID,
    bound_block_requirement_condition_is_closed,
)
from ..rules.source_references import SourceReferenceSpec
from .public_state_queries import fixed_public_state_parts


CAPABILITY = 'combat.requirement.bound_static_all_blockers'
HANDLER = 'ability.static.declaration-requirement-component.v1'


def _requirement(scope,condition):
    return DeclarationRequirementTemplate('intrinsic-all-able-blockers-v1','block','all_able_blockers',
        schema_version=2,scope=scope,condition=condition)


def _component(requirement):
    return {'handler_id':HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'fragment':ability_fragment_to_dict(requirement)}


def static_all_blocker_requirement_handler(text, *, source_name):
    material=' '.join(text.strip().split())
    if 'all creatures able to block ' not in material.casefold():
        return None
    condition=None
    parts=fixed_public_state_parts(material,source_name=source_name)
    if parts is not None:condition,material=parts
    if condition is not None and not bound_block_requirement_condition_is_closed(condition):
        return None
    pure=re.fullmatch(r'All creatures able to block (?P<subject>enchanted creature|equipped creature|this creature|.+?) do so\.?',material,re.I)
    if pure is not None:
        subject=pure['subject']
        attached=subject.casefold() in {'enchanted creature','equipped creature'}
        if not attached and not (subject.casefold()=='this creature' or SourceReferenceSpec(source_name).matches(subject)):
            return None
        if not attached and condition is None:
            return None
        requirement=_requirement('attached' if attached else 'self',condition)
        return 'static-bound-all-blocker-requirement-v2',_component(requirement),tuple(sorted({
            CAPABILITY,DECLARATION_COMPONENT_CAPABILITY_ID,
            *(('continuous.characteristics.fixed_public_state',) if condition is not None else ())}))
    mixed=re.fullmatch(r'(?P<characteristics>.+?),? and all creatures able to block it do so\.?',material,re.I)
    if mixed is None:return None
    body=mixed['characteristics'].rstrip(', ')
    if re.match(r'^it\b',body,re.I):
        if condition is None or not any(leaf.kind.value=='attached_matches_query' for leaf in condition.leaf_conditions()):return None
        body=re.sub(r'^it\b','Enchanted creature',body,count=1,flags=re.I)
    if not re.match(r'^(?:Enchanted|Equipped) creature\b',body,re.I):return None
    from .continuous_templates import attached_fixed_characteristics_handler,_conditional_target,FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
    if condition is None:
        compiled=attached_fixed_characteristics_handler(body+'.',source_name=source_name)
        if compiled is None:return None
        descriptor=compiled[1];required=set(compiled[2])
    else:
        compiled=_conditional_target(body+'.',source_name=source_name)
        if compiled is None:return None
        target,modifier,dependencies=compiled
        descriptor={'handler_id':FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,'schema_version':1,
            'event':'characteristics.evaluate','source_condition':condition.to_dict(),'target':target,'modifier':modifier}
        required={*dependencies,'continuous.characteristics.fixed_public_state'}
    requirement=_requirement('attached',condition)
    return 'static-characteristics-bound-all-blocker-requirement-v2',(descriptor,_component(requirement)),tuple(sorted({
        *required,CAPABILITY,DECLARATION_COMPONENT_CAPABILITY_ID}))
