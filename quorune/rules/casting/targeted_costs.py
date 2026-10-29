from __future__ import annotations

from ...target_source import spell_targeting_source

"""Target-dependent costs around the existing cost and target authorities."""

from itertools import combinations
from ...targets import available_modes,target_plan
from ...selection.target_validation import (validate_grouped_target_assignment,
    target_assignment_witnesses, public_target_schema_for_witnesses)
from ...semantic_runtime.targeted_spell_costs import targeted_spell_cost_sources
from ...errors import GameRuleError


def target_cost_fact(sources,targets):
    applied=tuple((source,source.adjustment(targets)) for source in sources if source.applies(targets))
    counted=any(source.spec.schema_version==5 for source in sources)
    reduction=counted or any(source.spec.generic<0 for source in sources)
    return {'schema_version':3 if counted else 2 if reduction else 1,'generic':sum(max(0,amount) for source,amount in applied),
        **({'generic_reduction':sum(max(0,-amount) for source,amount in applied)} if reduction else {}),
        'sources':[{'component_id':source.component_id,'source_ref':source.source_ref,'source_identity':source.source_identity,
            'controller':source.controller,'generic':amount} for source,amount in applied]}


def _cost_signature(fact):
    return tuple((row['component_id'],row['generic']) for row in fact['sources'])


def _target_assignments(host,seat,card,schema,modes,*,required=None,extra_targets=0,sources=None):
    if schema is None:yield (),{};return
    plan=target_plan(schema,modes,require_modes=bool(available_modes(schema)))
    candidates=host._target_candidate_map(seat,plan,acting_source=spell_targeting_source(host,card), source_ref=card.ref)
    eligible_sets=None
    if sources is not None:
        extra_targets=max(extra_targets,sum(group.max_targets-group.min_targets for group in plan.groups))
        refs={ref for values in candidates.values() for ref in values}
        eligible_sets=tuple(frozenset(ref for ref in refs if source.matches_target(ref)) for source in sources)
    yield from target_assignment_witnesses(host,plan,candidates,required=required,
        extra_targets=extra_targets,eligible_sets=eligible_sets)


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
        for applied,chosen in states.items():
            grouped={group.group_id:list(chosen)}
            try:targets=validate_grouped_target_assignment(host,plan,grouped,candidates)
            except (ValueError,GameRuleError):continue
            compatible=sum(signature<=applied for signature in signatures.values())
            yield tuple(targets),grouped,compatible


def _announced_tax_variants(host,seat,card,schema,sources,option,modes):
    """Price each signature once, retaining its separate target witnesses."""
    signatures={}
    if 'target_count_price' in option:
        from .target_count_costs import counted_target_tax_assignments
        assignments=counted_target_tax_assignments(host,seat,card,schema,sources,option['target_count_price'])
    else:
        from .distinct_target_costs import has_distinct_target_modifiers,distinct_target_tax_assignments
        assignments=(distinct_target_tax_assignments(host,seat,card,schema,sources)
            if has_distinct_target_modifiers(sources) else _announced_tax_assignments(host,seat,card,schema,sources))
    for targets,grouped,limit in assignments:
        fact=target_cost_fact(sources,targets)
        key=_cost_signature(fact)
        value=signatures.setdefault(key,[fact,[],limit]);value[2]=max(value[2],limit)
        value[1].append({'targets':grouped,'modes':list(modes)})
    for fact,witnesses,limit in signatures.values():
        smallest=min(witnesses,key=lambda w:sum(len(refs) for refs in w['targets'].values()))
        yield {**option,'targeted_cost':fact,'target_cost_witness':smallest,'target_count_preview_limit':limit,
            'target_cost_witnesses':witnesses}


def target_cost_variants(host,seat,card,program,option,response,*,hint):
    from ...morph import FACE_DOWN_CAST_METHODS
    sources=targeted_spell_cost_sources(host,seat,card=card if option.get('id') not in FACE_DOWN_CAST_METHODS else None,spell_program=program)
    if not sources and 'target_count_price' not in option:yield option;return
    schema=option.get('target_schema') or getattr(program,'target_schema',None)
    if schema is None:
        enchant=host._compiled_enchant_spec(card)
        if enchant is not None:schema=enchant.target_schema()
    modes=tuple(response.get('modes') or ())
    if not hint or 'targets' in response:
        targets,_=host._validate_semantic_targets(seat,program,host._normalize_target_submission(response.get('targets')),
            modes=modes,acting_source=spell_targeting_source(host,card), source_ref=card.ref,target_schema=schema)
        yield {**option,'targeted_cost':target_cost_fact(sources,targets)};return
    if 'target_count_preview_limit' in option or 'target_count_price' in option:
        yield from _announced_tax_variants(host,seat,card,schema,sources,option,modes)
        return
    mode_sets=(modes,)
    if schema is not None and available_modes(schema) and not modes:
        names=available_modes(schema);minimum=int(schema.get('min_modes',schema.get('mode_count',1)));maximum=int(schema.get('max_modes',schema.get('mode_count',1)))
        mode_sets=tuple(value for count in range(minimum,maximum+1) for value in combinations(names,count))
    seen=set()
    from .distinct_target_costs import has_distinct_target_modifiers
    counted=has_distinct_target_modifiers(sources)
    for selected in mode_sets:
        required=(None,)
        if schema is not None:
            plan=target_plan(schema,selected,require_modes=bool(available_modes(schema)))
            candidates=host._target_candidate_map(seat,plan,acting_source=spell_targeting_source(host,card), source_ref=card.ref)
            required=(*required,*((group.group_id,ref) for group in plan.groups for ref in candidates[group.group_id] if group.max_targets))
        for needed in required:
            for targets,grouped in _target_assignments(host,seat,card,schema,selected,required=needed,
                    extra_targets=len(sources),sources=sources if counted else None):
                fact=target_cost_fact(sources,targets);key=(selected,needed,_cost_signature(fact))
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
    return fact if 'target_count_parameter' in option or fact is not None and fact.get('schema_version') in {2,3} else None


def _preview_option_rank(option):
    raw=option.to_dict()
    version=raw.get('targeted_cost',{}).get('schema_version')
    maximum=raw.get('choice_schema',{}).get('x',{}).get('maximum',0) if version in {2,3} else 0
    if version==3:return -maximum,sum(raw['requirements'].values())
    return sum(raw['requirements'].values()),-maximum


def target_cost_clauses(host,seat,*,card=None,spell_program=None):
    return [{'generic':source.spec.generic,'any_target_refs':list(source.eligible_target_refs),
        'source':source.source_ref,'component_id':source.component_id,
        **({'counted_targets':source.spec.counted_targets} if source.spec.schema_version==5 else {})}
        for source in targeted_spell_cost_sources(host,seat,card=card,spell_program=spell_program)]


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
            count=raw.get('target_count_price')
            amounts=tuple(row['generic'] for row in raw['targeted_cost']['sources'])
            prices.setdefault(option.option_id,{})[(modes,components,count,amounts)]={'modes':list(modes),'components':list(components),
                **({'generic_adjustments':[{'component_id':row['component_id'],'generic':row['generic']}
                    for row in raw['targeted_cost']['sources']]} if raw['targeted_cost'].get('schema_version')==3 else {}),
                **({'target_count':count} if count is not None else {}),
                'requirements':raw['requirements'], **({'maximum_x':raw['choice_schema']['x']['maximum']}
                if ('target_count_parameter' in raw or raw['targeted_cost'].get('schema_version') in {2,3}) and 'x' in raw.get('choice_schema',{}) else {})}
        prior=result.get(option.option_id)
        if prior is None or _preview_option_rank(option)<_preview_option_rank(prior):result[option.option_id]=option
    return tuple(CastCostOption.from_dict({**option.to_dict(),**({'payable_target_witnesses':witnesses[oid],
        'target_cost_prices':list(prices.get(oid,{}).values())} if oid in witnesses else {})}) for oid,option in result.items())


def public_target_costs(host,seat,option,*,card=None,spell_program=None):
    from ...morph import FACE_DOWN_CAST_METHODS
    clauses=target_cost_clauses(host,seat,card=card if option.get('id') not in FACE_DOWN_CAST_METHODS else None,spell_program=spell_program)
    if not clauses and 'target_count_cost' not in option:return None
    def label(requirements):
        value=(f"{{{requirements['GENERIC']}}}" if requirements.get('GENERIC') else '')
        value+=''.join(('{'+key+'}')*requirements.get(key,0) for key in 'WUBRGC')
        return 'Pay '+(value or '{0}')
    kind=('generic_adjustment_per_distinct_target' if any('counted_targets' in row for row in clauses)
        else 'generic_adjustment_once_per_source' if any(row['generic']<0 for row in clauses) else 'generic_increase_once_per_source')
    return {'kind':kind,'clauses':clauses,
        'prices':[{**row,'label':f"Choose X up to {row['maximum_x']}" if option.get('targeted_cost',{}).get('schema_version') in {2,3} and 'maximum_x' in row
            else label(row['requirements'])} for row in option.get('target_cost_prices',())]}


def public_payable_target_schema(schema,option):
    witnesses=option.get('payable_target_witnesses')
    if witnesses is None:return schema
    return public_target_schema_for_witnesses(schema,witnesses)
