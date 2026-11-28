from __future__ import annotations

"""Seal one public query maximum in the existing stack amount cache."""

from collections.abc import Mapping
from ..library_search_model import (
    QueryBoundLibrarySearchSpec,QUERY_BOUND_SEARCH_RESOLVED,
)
from ..query_effect_amount_model import freeze_query_bound_search_amount
from ..scalar_effect_amount_model import ScalarEffectAmountSpec
from ..scalar_effect_amounts import resolve_scalar_effect_amount
from ..semantic_runtime.query_effect_amounts import resolve_public_query_amount
from ..errors import GameRuleError


def materialize_query_bound_search(host,item,effect,instruction_pointer):
    try:
        spec=QueryBoundLibrarySearchSpec.from_effect(effect,allow_resolved=True)
        if type(instruction_pointer) is not int or instruction_pointer<0:
            raise ValueError('Search quantity requires its exact instruction pointer')
        binding='query-search:'+str(instruction_pointer)
        frozen = freeze_query_bound_search_amount(spec.amount, binding)
        if isinstance(frozen, ScalarEffectAmountSpec):
            cache_key = 'declared_scalar_amounts'
            resolve_amount = resolve_scalar_effect_amount
        else:
            cache_key = 'declared_public_amounts'
            resolve_amount = resolve_public_query_amount
        if QUERY_BOUND_SEARCH_RESOLVED in effect:
            cache = item.context.get(cache_key)
            if (effect[QUERY_BOUND_SEARCH_RESOLVED] != frozen.to_dict()
                    or not isinstance(cache, Mapping) or binding not in cache):
                raise ValueError('Saved search quantity has no matching frozen stack value')
        maximum = max(0, resolve_amount(host, frozen.to_dict(), item))
        if QUERY_BOUND_SEARCH_RESOLVED in effect:
            if effect['count']['maximum']!=maximum:
                raise ValueError('Saved search maximum differs from its frozen public value')
            return dict(effect)
        return spec.resolved_effect(maximum,frozen)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
