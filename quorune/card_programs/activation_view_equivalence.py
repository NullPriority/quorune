from __future__ import annotations

"""Source-pinned precedence for equal activated catalogs, prices and results."""

import hashlib
from .current_view_equivalence import _catalog_preserves_cost,_same_current_source
from .battlefield_search_equivalence import fixed_battlefield_search_results_equal


def _predefined_token_results_equal(current,old):
    from ..compiler.token_templates import fixed_token_creation_effect_template
    from ..rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
    if len(current.effects)!=1 or list(old.effects)!=[{'op':'create_treasure','controller':'$controller'}]:
        return False
    # Use the existing complete predefined definition, rather than a subset of
    # token characteristics or an independently maintained token definition.
    template=fixed_token_creation_effect_template('Create a Treasure token.')
    if template is None or dict(current.effects[0])!=template.effect:return False
    required=fixed_token_creation_node_capabilities(effects=current.effects,
        target_schema=current.target_schema,mechanic_ids=current.coverage)
    return bool(required) and set(required)<=set(current.capability_dependencies)


def _results_equal(current,old):
    from .reviewed_overlay import _canonical_effects
    return (_canonical_effects(current)==_canonical_effects(old)
        or fixed_battlefield_search_results_equal(current,old)
        or _predefined_token_results_equal(current,old))


def shadowed_reviewed_activation_view_keys(record,generated_programs,reviewed_programs,*,capability_registry=None,capability_profile='traditional'):
    if capability_registry is None or record.layout!='normal' or record.faces:return set()
    from .binding import bind_semantic_program_runtime
    generated=tuple(generated_programs);reviewed=tuple(reviewed_programs);shadowed=set()
    oracle_hash=hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    for old in reviewed:
        if old.event!='activate' or not old.ability_id.startswith('ability:'):continue
        matches=[]
        for current in generated:
            if (current.key!=old.key or current.ability_id!=old.ability_id
                    or not _same_current_source(record,current,old)
                    or not str(current.provenance.get('authored_by','')).startswith('oracle-ir-v')
                    or current.provenance.get('source_oracle_hash')!=oracle_hash
                    or current.semantic_schema_version!=old.semantic_schema_version
                    or current.event_condition!=old.event_condition or current.trigger_limit!=old.trigger_limit
                    or current.requires_arbiter or current.capability_closure.get('blockers')!=[]
                    or not _catalog_preserves_cost(current,old)
                    or not _results_equal(current,old)):continue
            if bind_semantic_program_runtime(current,capability_registry=capability_registry,profile=capability_profile)['strict']:
                matches.append(current)
        if len(matches)==1:shadowed.add(old.key)
    return shadowed
