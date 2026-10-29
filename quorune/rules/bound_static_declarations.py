from __future__ import annotations

"""Project current giver-owned rules onto real, current declaration anchors."""

from dataclasses import replace
from ..bound_declaration_model import BoundStaticDeclarationRulesSpec
from ..declaration_fragments import DeclarationRequirementTemplate
from ..object_query import object_matches_query
from ..object_query_union import bind_receiver_union_source
from ..characteristic_read_frames import characteristic_read_frame


def current_bound_declaration_rules(host,receiver=None,*,error_type):
    result=[]
    with characteristic_read_frame(host):
        for giver in sorted(host.state.cards.values(),key=lambda card:card.ref):
            if giver.zone!='battlefield' or giver.phased_out:continue
            specs=host._effective_ability_fragments(giver,error_type=error_type)
            for index,spec in enumerate(specs):
                if not isinstance(spec,BoundStaticDeclarationRulesSpec):continue
                if spec.source_condition is not None and not host._fixed_public_state_condition_holds(giver,spec.source_condition,require_available=True):continue
                query=replace(bind_receiver_union_source(spec.query,giver.ref),zones=('battlefield',),
                    controller=giver.controller if spec.controller=='source_controller' else None,
                    owner=giver.controller if spec.controller=='source_owner' else None,
                    excluded_controllers=(giver.controller,) if spec.controller=='source_opponents' else (),
                    exclude_ref=giver.ref if spec.exclude_source else None)
                candidates=([receiver] if receiver is not None else
                    [giver] if spec.scope=='source' else
                    [host.state.cards.get(giver.attached_to or '')] if spec.scope=='attached' else host.state.cards.values())
                for anchor in candidates:
                    if anchor is None or anchor.zone!='battlefield' or anchor.phased_out:continue
                    if spec.scope=='source' and anchor.object_id!=giver.object_id:continue
                    if spec.scope=='attached' and anchor.object_id!=giver.attached_to:continue
                    if not object_matches_query(host._public_object_query_result(anchor),query):continue
                    for ordinal,rule in enumerate(spec.rules):
                        key=f'bound:{giver.logical_object_id}:{index}:{anchor.logical_object_id}:{ordinal}'
                        result.append((key,giver,anchor,rule))
    return tuple(result)


def bound_declaration_requirements(host,receiver,*,error_type):
    return tuple((key,rule) for key,_giver,_anchor,rule in current_bound_declaration_rules(host,receiver,error_type=error_type)
        if isinstance(rule,DeclarationRequirementTemplate))
