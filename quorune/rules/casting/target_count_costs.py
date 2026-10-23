from __future__ import annotations

"""Target-count prices use the existing source, target, and mana authorities."""

from copy import deepcopy
from collections.abc import Mapping

from ...card_programs.admission import program_has_complete_card_program_admission
from ...target_count_cost_model import FixedManaPerTargetCostSpec, TARGET_COUNT_COST_FIELD, TARGET_COUNT_COST_HANDLER


def compiled_target_count_cost(host, card):
    from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
    record=host.card_record(card)
    if record is None or record.layout!='normal' or record.faces or card.annotations.get('copy_overrides') is not None:
        return None
    found=[]
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='all',event='cast.cost'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id')==TARGET_COUNT_COST_HANDLER:
                found.extend(default_cast_cost_component_registry().lower(descriptor,None))
    return found[0] if len(found)==1 else None


def target_count_price_variants(host,seat,card,program,option,response,*,hint):
    spec=compiled_target_count_cost(host,card)
    if spec is None:yield option;return
    if hint:
        from ...target_source import spell_targeting_source
        schema=option.get('target_schema') or getattr(program,'target_schema',None)
        public=host._public_target_schema(seat,schema,acting_source=spell_targeting_source(host,card),source_ref=card.ref) if schema else None
        if public is None or len(public.get('groups',()))!=1:return
        group,=public['groups']
        counts=range(group['min'],min(group['max'],len(group['legal_refs']))+1)
    else:
        counts=(len(host._normalize_target_submission(response.get('targets'))),)
    for count in counts:
        priced=deepcopy(option)
        for key,amount in spec.requirements_for_count(count).items():priced['requirements'][key]+=amount
        priced.update(target_count_price=count,target_count_cost={'spec':spec.to_dict(),'count':count})
        yield priced


def validate_target_count_cost_commit(host,card,option,targets):
    spec=compiled_target_count_cost(host,card)
    fact=option.get(TARGET_COUNT_COST_FIELD)
    if spec is None and fact is None:return
    if (not isinstance(fact,Mapping) or set(fact)!={'spec','count'}
            or type(fact['count']) is not int or fact['count']<0):
        raise ValueError('Per-target additional mana fact is malformed')
    sealed=FixedManaPerTargetCostSpec.from_dict(fact['spec'])
    if spec is None or spec!=sealed or fact['count']!=len(targets):
        raise ValueError('Per-target additional mana contract changed before commitment')


def counted_target_tax_assignments(host,seat,card,schema,sources,count):
    """One witness per tax signature/count/ref, without enumerating subsets."""
    from ...targets import target_plan
    from ...target_source import spell_targeting_source
    from ...selection.target_validation import validate_grouped_target_assignment
    from ...errors import GameRuleError
    plan=target_plan(schema)
    if len(plan.groups)!=1:return
    group,=plan.groups
    candidates=host._target_candidate_map(seat,plan,acting_source=spell_targeting_source(host,card),source_ref=card.ref)
    refs=candidates[group.group_id]
    if not group.min_targets<=count<=min(group.max_targets,len(refs)):return
    signatures={ref:frozenset(s.component_id for s in sources if ref in s.eligible_target_refs) for ref in refs}
    for required in (None,) if count==0 else refs:
        initial=() if required is None else (required,)
        initial_signature=frozenset() if required is None else signatures[required]
        states={(len(initial),initial_signature):initial}
        buckets={}
        for ref in refs:
            if ref==required:continue
            if group.same_owner and required is not None and host._target_snapshot(ref).get('owner')!=host._target_snapshot(required).get('owner'):continue
            buckets.setdefault(signatures[ref],[]).append(ref)
        for signature,values in buckets.items():
            updated={}
            for (size,applied),chosen in states.items():
                for take in range(min(count-size,len(values))+1):
                    key=(size+take,applied|signature if take else applied)
                    updated.setdefault(key,(*chosen,*values[:take]))
            states=updated
        for (size,applied),chosen in states.items():
            if size!=count:continue
            grouped={group.group_id:list(chosen)}
            try:targets=validate_grouped_target_assignment(host,plan,grouped,candidates)
            except (ValueError,GameRuleError):continue
            yield tuple(targets),grouped,count
