from __future__ import annotations

"""Complete printed Kicker costs lowered through the shared casting algebra."""

import re
from ..nonmana_kicker import FixedNonmanaKickerSpec,NONMANA_KICKER_HANDLER,nonmana_kicker_dependencies
from ..util import mana_cost_to_vector
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .spell_additional_cost_templates import (fixed_zone_change_additional_cost_template,
    fixed_sacrifice_additional_cost_template,fixed_life_payment_additional_cost_template)


def compile_nonmana_kicker(material_line,*,oracle_line,line_index):
    match=re.fullmatch(r'Kicker—(?P<cost>.+?)\.?',material_line.strip(),re.I)
    if match is None:return None
    cost=match['cost'];mana_text=''
    prefix=re.fullmatch(r'(?P<mana>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+), (?P<rest>.+)',cost,re.I)
    if prefix is not None:mana_text=prefix['mana'].upper();body=prefix['rest']
    else:body=cost
    sentence='As an additional cost to cast this spell, '+body[0].lower()+body[1:]+'.'
    parsed=fixed_zone_change_additional_cost_template(sentence,allow_counted=True)
    if parsed is None:parsed=fixed_sacrifice_additional_cost_template(sentence)
    if parsed is None:parsed=fixed_life_payment_additional_cost_template(sentence)
    if parsed is None:
        tap=re.fullmatch(r'Tap an untapped (?P<subtype>[A-Za-z][A-Za-z\'-]*) you control',body,re.I)
        if tap is None:return None
        from ..creature_subtypes import canonical_creature_subtype
        from ..rules.tap_additional_cost import FixedTapAdditionalCost
        subtype=canonical_creature_subtype(tap['subtype'])
        if subtype is None:return None
        descriptor=FixedTapAdditionalCost(1,('creature',),schema_version=2,creature_subtypes=(subtype,)).to_descriptor()
    else:
        descriptor=dict(parsed.descriptor)
        if descriptor.get('kind')=='sacrifice':
            from .spell_additional_cost_templates import FixedZoneChangeAdditionalCostTemplate
            from ..additional_cost_vocabulary import SACRIFICE_ONE_COST
            from ..object_predicate import ObjectQuerySpec
            descriptor=dict(FixedZoneChangeAdditionalCostTemplate(SACRIFICE_ONE_COST,
                ObjectQuerySpec.from_dict(descriptor['predicate'])).descriptor)
    mana,complex_symbols=mana_cost_to_vector(mana_text)
    if complex_symbols:return None
    try:return FixedNonmanaKickerSpec(f'ab{line_index+1}',line_index,oracle_line,cost,mana,(descriptor,))
    except (TypeError,ValueError):return None


def nonmana_kicker_keyword_node(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode,append_residual
    spec=compile_nonmana_kicker(material_line,oracle_line=line,line_index=span.line-1)
    if spec is None:return None
    dependencies=nonmana_kicker_dependencies(spec)
    gate=explicit_capabilities_gate(dependencies,capability_registry=capability_registry,capability_profile=capability_profile)
    residual_ids=()
    if gate.blockers:
        residual_ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
            reason='Fixed nonmana Kicker requires complete represented payment authority',blockers=gate.blockers),)
    handler={'handler_id':NONMANA_KICKER_HANDLER,'schema_version':1,'event':'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD:True,'kicker':spec.to_dict()}
    return OracleNode(node_id=node_id,kind='keyword_ability',text=line,span=span,active_zone='all',event='cast.cost',
        lowerable=True,exact=not gate.blockers,template_id='single-fixed-nonmana-kicker-v1',handlers=(handler,),
        mechanics=('kicker',),runtime_coverage=('optional_additional_nonmana_cost','kicked_cast_fact'),
        residual_ids=residual_ids,capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
