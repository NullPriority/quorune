from __future__ import annotations

"""Disjoint absent-keyword selectors over the existing layer-six grant owner."""

from dataclasses import dataclass
from .continuous_components import FixedQueryKeywordGrantHandler
from .context import SemanticNodeError

NEGATIVE_KEYWORD_GRANT_CAPABILITY='continuous.ability.disjoint_negative_keyword_grant'
NEGATIVE_KEYWORD_GRANT_HANDLER='continuous.ability.disjoint-negative-keyword-grant.v1'


@dataclass(frozen=True,slots=True)
class DisjointNegativeKeywordGrantHandler(FixedQueryKeywordGrantHandler):
    handler_id:str=NEGATIVE_KEYWORD_GRANT_HANDLER
    family:str=NEGATIVE_KEYWORD_GRANT_CAPABILITY
    capability_dependencies:tuple[str,...]=(NEGATIVE_KEYWORD_GRANT_CAPABILITY,'continuous.ability.fixed_query_keyword_grant')

    def validate(self,descriptor):
        node=super(DisjointNegativeKeywordGrantHandler,self).validate(descriptor)
        if (len(node.predicate.keywords_none)!=1 or node.fragments
                or set(node.predicate.keywords_none).intersection(a.casefold() for a in node.abilities)):
            raise SemanticNodeError('Negative keyword grants require one tested absence and disjoint ordinary additions')
        return node
