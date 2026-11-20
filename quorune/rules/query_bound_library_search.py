from __future__ import annotations

"""Seal one public query maximum in the existing stack amount cache."""

from dataclasses import replace
from ..library_search_model import (
    QueryBoundLibrarySearchSpec,QUERY_BOUND_SEARCH_RESOLVED,
)
from ..query_effect_amount_model import PublicQueryAmountSpec
from ..semantic_runtime.query_effect_amounts import resolve_public_query_amount
from ..errors import GameRuleError


def materialize_query_bound_search(host,item,effect,instruction_pointer):
    try:
        spec=QueryBoundLibrarySearchSpec.from_effect(effect,allow_resolved=True)
        if type(instruction_pointer) is not int or instruction_pointer<0:
            raise ValueError('Search quantity requires its exact instruction pointer')
        binding='query-search:'+str(instruction_pointer)
        if QUERY_BOUND_SEARCH_RESOLVED in effect:
            frozen=PublicQueryAmountSpec.from_dict(effect[QUERY_BOUND_SEARCH_RESOLVED])
            if frozen.binding_id!=binding or binding not in item.context.get('declared_public_amounts',{}):
                raise ValueError('Saved search quantity has no matching frozen stack value')
        else:frozen=replace(spec.amount,schema_version=2,binding_id=binding)
        maximum=max(0,resolve_public_query_amount(host,frozen.to_dict(),item))
        if QUERY_BOUND_SEARCH_RESOLVED in effect:
            if effect['count']['maximum']!=maximum:
                raise ValueError('Saved search maximum differs from its frozen public value')
            return dict(effect)
        return spec.resolved_effect(maximum,frozen)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
