from __future__ import annotations

"""Fixed Embalm/Eternalize activation composed from existing cost and copy owners."""

import re
from ..abilities import ActivatedAbility
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..token_copy_recipes import TokenCopyExceptionSpec,TokenCopyRecipeSpec
from ..util import mana_cost_to_vector
from ..semantic_runtime.activated_abilities import activated_ability_catalog_descriptor
from ..semantic_runtime.graveyard_copy_keyword import GRAVEYARD_COPY_KEYWORD_CAPABILITY,GRAVEYARD_COPY_KEYWORD_HANDLER
from .activated_costs import activated_ability_cost,activated_ability_cost_capabilities
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode,append_residual

_COST=r'(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+'


def graveyard_copy_keyword_node(*,node_id,line,material_line,span,mechanics,capability_registry,capability_profile,residuals,**_unused):
    if mechanics not in (('embalm',),('eternalize',)):return None
    keyword=mechanics[0];match=re.fullmatch(rf'{keyword}\s+(?P<cost>{_COST})\.?',material_line,re.I)
    if match is None:return None
    cost=match['cost'];eternal=keyword=='eternalize';mana,_complex_symbols=mana_cost_to_vector(cost)
    ability=ActivatedAbility(ability_id=f'ab{span.line}',line_index=span.line-1,oracle_line=line,
        cost_text=cost+', Exile this card from your graveyard',effect_text='Create a token copy of this card.',zones=('graveyard',),
        mana=mana,exile_source=True,sorcery_speed=True)
    exception=TokenCopyExceptionSpec(add_subtypes=('zombie',),colors=('B',) if eternal else ('W',),
        power=4 if eternal else None,toughness=4 if eternal else None,remove_mana_cost=True,schema_version=2)
    recipe=TokenCopyRecipeSpec(origin='source',exception=exception,**({'schema_version':2,'creation_origin':'embalm'} if not eternal else {}))
    caps=(GRAVEYARD_COPY_KEYWORD_CAPABILITY,'token.creation.fixed_copy',*activated_ability_cost_capabilities(ability))
    gate=explicit_capabilities_gate(caps,capability_registry=capability_registry,capability_profile=capability_profile)
    ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,reason='Graveyard copy activation requires complete canonical cost and token-copy capabilities.',blockers=gate.blockers),) if gate.blockers else ()
    descriptor=activated_ability_catalog_descriptor(ability)
    marker={'handler_id':GRAVEYARD_COPY_KEYWORD_HANDLER,'schema_version':1,'event':'activate','keyword':keyword,REQUIRES_COMPLETE_CARD_PROGRAM_FIELD:True}
    return OracleNode(node_id=node_id,kind='activated_ability',text=line,span=span,active_zone='graveyard',event='activate',
        lowerable=True,exact=not ids,template_id='fixed-graveyard-copy-keyword-v1',cost=activated_ability_cost(ability),
        effects=({'op':'create_token','controller':'$controller','quantity':1,'copy_spec':recipe.to_dict()},),
        handlers=(descriptor,marker),runtime_coverage=('graveyard_activation','fixed-token-copy'),
        mechanics=(*mechanics,'cr-111-tokens','fixed-token-production','fixed-token-copy','cr-707-copying-objects'),
        residual_ids=ids,capability_dependencies=gate.capabilities,capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
