from __future__ import annotations

from ...target_source import spell_targeting_source

"""Target-dependent costs around the existing cost and target authorities."""

from itertools import combinations,combinations_with_replacement,product
from ...targets import available_modes,target_plan
from ...selection.target_validation import validate_grouped_target_assignment
from ...semantic_runtime.targeted_spell_costs import targeted_spell_cost_sources
from ...errors import GameRuleError


def target_cost_fact(sources,targets):
    applied=tuple(source for source in sources if source.applies(targets))
    reduction=any(source.spec.generic<0 for source in sources)
    return {'schema_version':2 if reduction else 1,'generic':sum(max(0,source.spec.generic) for source in applied),
        **({'generic_reduction':sum(max(0,-source.spec.generic) for source in applied)} if reduction else {}),
        'sources':[{'component_id':source.component_id,'source_ref':source.source_ref,'source_identity':source.source_identity,
            'controller':source.controller,'generic':source.spec.generic} for source in applied]}


def _target_assignments(host,seat,card,schema,modes,*,required=None,extra_targets=0):
    if schema is None:yield (),{};return
    plan=target_plan(schema,modes,require_modes=bool(available_modes(schema)))
    candidates=host._target_candidate_map(seat,plan,acting_source=spell_targeting_source(host,card), source_ref=card.ref)
    choices=[]
    for group in plan.groups:
        refs=candidates[group.group_id]
        choose=combinations if group.distinct and not group.allow_reuse else combinations_with_replacement
        minimum=max(group.min_targets,1 if required is not None and required[0]==group.group_id else 0)
        maximum=min(group.max_targets,len(refs),minimum+extra_targets)
        choices.append(tuple(value for count in range(minimum,maximum+1) for value in choose(refs,count)
            if required is None or required[0]!=group.group_id or required[1] in value))
    for values in product(*choices):
        grouped={group.group_id:list(refs) for group,refs in zip(plan.groups,values)}
        try:targets=validate_grouped_target_assignment(host,plan,grouped,candidates)
        except (ValueError,GameRuleError):continue
        yield tuple(targets),grouped


def _announced_tax_assignments(host,seat,card,schema,sources):
    """Minimal witnesses per tax signature, without enumerating target subsets.

    Announced cardinality has one homogeneous distinct group. All references
    with the same source signature incur the same tax; retaining each required
    reference preserves offer availability while unioning signatures once.
    """
    plan=target_plan(schema)
    if len(plan.groups)!=1:return
    group,=plan.groups
    candidates=host._target_candidate_map(seat,plan,
        acting_source=spell_targeting_source(host,card),source_ref=card.ref)
    refs=candidates[group.group_id]
    signatures={ref:frozenset(source.component_id for source in sources
        if ref in source.eligible_target_refs) for ref in refs}
    representatives={}
    for ref in refs:representatives.setdefault(signatures[ref],ref)
    for required in (None,*refs):
        initial=() if required is None else (required,)
        states={frozenset() if required is None else signatures[required]:initial}
        for signature,ref in representatives.items():
            for applied,chosen in tuple(states.items()):
                union=applied|signature
                if union==applied:continue
                witness=(*chosen,ref)
                if union not in states or len(witness)<len(states[union]):states[union]=witness
        for chosen in states.values():
            grouped={group.group_id:list(chosen)}
            try:targets=validate_grouped_target_assignment(host,plan,grouped,candidates)
            except (ValueError,GameRuleError):continue
            yield tuple(targets),grouped


def _announced_tax_variants(host,seat,card,schema,sources,option,modes):
    """Price each signature once, retaining its separate target witnesses."""
    signatures={}
    for targets,grouped in _announced_tax_assignments(host,seat,card,schema,sources):
        fact=target_cost_fact(sources,targets)
        key=tuple(row['component_id'] for row in fact['sources'])
        signatures.setdefault(key,(fact,[]))[1].append({'targets':grouped,'modes':list(modes)})
    for fact,witnesses in signatures.values():
        smallest=min(witnesses,key=lambda w:sum(len(refs) for refs in w['targets'].values()))
        yield {**option,'targeted_cost':fact,'target_cost_witness':smallest,
            'target_cost_witnesses':witnesses}


def target_cost_variants(host,seat,card,program,option,response,*,hint):
    from ...morph import FACE_DOWN_CAST_METHODS
    sources=targeted_spell_cost_sources(host,seat,card=card if option.get('id') not in FACE_DOWN_CAST_METHODS else None,spell_program=program)
    if not sources:yield option;return
    schema=option.get('target_schema') or getattr(program,'target_schema',None)
    if schema is None:
        enchant=host._compiled_enchant_spec(card)
        if enchant is not None:schema=enchant.target_schema()
    modes=tuple(response.get('modes') or ())
    if not hint or 'targets' in response:
        targets,_=host._validate_semantic_targets(seat,program,host._normalize_target_submission(response.get('targets')),
            modes=modes,acting_source=spell_targeting_source(host,card), source_ref=card.ref,target_schema=schema)
        yield {**option,'targeted_cost':target_cost_fact(sources,targets)};return
    if 'target_count_preview_limit' in option:
        yield from _announced_tax_variants(host,seat,card,schema,sources,option,modes)
        return
    mode_sets=(modes,)
    if schema is not None and available_modes(schema) and not modes:
        names=available_modes(schema);minimum=int(schema.get('min_modes',schema.get('mode_count',1)));maximum=int(schema.get('max_modes',schema.get('mode_count',1)))
        mode_sets=tuple(value for count in range(minimum,maximum+1) for value in combinations(names,count))
    seen=set()
    for selected in mode_sets:
        required=(None,)
        if schema is not None:
            plan=target_plan(schema,selected,require_modes=bool(available_modes(schema)))
            candidates=host._target_candidate_map(seat,plan,acting_source=spell_targeting_source(host,card), source_ref=card.ref)
            required=(*required,*((group.group_id,ref) for group in plan.groups for ref in candidates[group.group_id] if group.max_targets))
        for needed in required:
            for targets,grouped in _target_assignments(host,seat,card,schema,selected,required=needed,extra_targets=len(sources)):
                fact=target_cost_fact(sources,targets);key=(selected,needed,tuple(row['component_id'] for row in fact['sources']))
                if key in seen:continue
                seen.add(key)
                yield {**option,'targeted_cost':fact,'target_cost_witness':{'targets':grouped,'modes':list(selected)}}


def apply_target_cost_increase(option):
    fact=option.get('targeted_cost')
    if fact is None:return
    from ...targeted_spell_costs import validate_targeted_spell_cost_fact
    option['requirements']['GENERIC']+=validate_targeted_spell_cost_fact(fact)
    if fact['generic']:option.setdefault('cost_increases',[]).append({'kind':'targeted','count':fact['generic']})


def target_cost_for_x_preview(option):
    fact=option.get('targeted_cost')
    return fact if 'target_count_parameter' in option or fact is not None and fact.get('schema_version')==2 else None


def _preview_option_rank(option):
    raw=option.to_dict()
    maximum=raw.get('choice_schema',{}).get('x',{}).get('maximum',0) if raw.get('targeted_cost',{}).get('schema_version')==2 else 0
    return sum(raw['requirements'].values()),-maximum


def target_cost_clauses(host,seat,*,card=None,spell_program=None):
    return [{'generic':source.spec.generic,'any_target_refs':list(source.eligible_target_refs),
        'source':source.source_ref,'component_id':source.component_id} for source in targeted_spell_cost_sources(host,seat,card=card,spell_program=spell_program)]


def consolidate_target_cost_options(options,*,hint):
    if not hint:return tuple(options)
    from ..action_proposals import CastCostOption
    result={};witnesses={};prices={}
    for option in options:
        raw=option.to_dict()
        if 'target_cost_witness' in raw:
            maximum=raw.get('choice_schema',{}).get('x',{}).get('maximum')
            candidates=raw.get('target_cost_witnesses',[raw['target_cost_witness']])
            witnesses.setdefault(option.option_id,[]).extend(w for w in candidates
                if maximum is None or sum(len(refs) for refs in w['targets'].values())<=maximum)
        if 'targeted_cost' in raw:
            modes=tuple(raw.get('target_cost_witness',{}).get('modes',()))
            components=tuple(row['component_id'] for row in raw['targeted_cost']['sources'])
            prices.setdefault(option.option_id,{})[(modes,components)]={'modes':list(modes),'components':list(components),
                'requirements':raw['requirements'], **({'maximum_x':raw['choice_schema']['x']['maximum']}
                if ('target_count_parameter' in raw or raw['targeted_cost'].get('schema_version')==2) and 'x' in raw.get('choice_schema',{}) else {})}
        prior=result.get(option.option_id)
        if prior is None or _preview_option_rank(option)<_preview_option_rank(prior):result[option.option_id]=option
    return tuple(CastCostOption.from_dict({**option.to_dict(),**({'payable_target_witnesses':witnesses[oid],
        'target_cost_prices':list(prices.get(oid,{}).values())} if oid in witnesses else {})}) for oid,option in result.items())


def public_target_costs(host,seat,option,*,card=None,spell_program=None):
    from ...morph import FACE_DOWN_CAST_METHODS
    clauses=target_cost_clauses(host,seat,card=card if option.get('id') not in FACE_DOWN_CAST_METHODS else None,spell_program=spell_program)
    if not clauses:return None
    def label(requirements):
        value=(f"{{{requirements['GENERIC']}}}" if requirements.get('GENERIC') else '')
        value+=''.join(('{'+key+'}')*requirements.get(key,0) for key in 'WUBRGC')
        return 'Pay '+(value or '{0}')
    return {'kind':'generic_adjustment_once_per_source' if any(row['generic']<0 for row in clauses) else 'generic_increase_once_per_source','clauses':clauses,
        'prices':[{**row,'label':f"Choose X up to {row['maximum_x']}" if option.get('targeted_cost',{}).get('schema_version')==2 and 'maximum_x' in row
            else label(row['requirements'])} for row in option.get('target_cost_prices',())]}


def public_payable_target_schema(schema,option):
    witnesses=option.get('payable_target_witnesses')
    if witnesses is None:return schema
    from copy import deepcopy
    result=deepcopy(schema)
    def filter_groups(value,selected):
        allowed={}
        for witness in selected:
            for group,refs in witness['targets'].items():allowed.setdefault(group,set()).update(refs)
        for group in value.get('groups',[]):group['legal_refs']=[ref for ref in group['legal_refs'] if ref in allowed.get(group['id'],())]
        if 'legal_refs' in value:value['legal_refs']=[ref for ref in value['legal_refs'] if any(ref in refs for refs in allowed.values())]
    if 'mode_schemas' in result:
        for mode,value in result['mode_schemas'].items():filter_groups(value,[w for w in witnesses if mode in w['modes']])
        legal_modes={mode for witness in witnesses for mode in witness['modes']}
        result['legal_modes']=[mode for mode in result['legal_modes'] if mode in legal_modes]
        result['mode_schemas']={mode:value for mode,value in result['mode_schemas'].items() if mode in legal_modes}
        result['legal_refs']=list(dict.fromkeys(ref for value in result['mode_schemas'].values() for group in value.get('groups',()) for ref in group['legal_refs']))
    else:filter_groups(result,witnesses)
    return result
