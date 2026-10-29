from __future__ import annotations

"""Disjoint type-only query scopes for one prospective entry occurrence."""

from dataclasses import dataclass, replace
from itertools import product

from .object_predicate import ObjectQuerySpec


_TYPE_FIELDS = ('types_all', 'types_any', 'excluded_types', 'subtypes_all',
                'subtypes_any', 'excluded_subtypes', 'supertypes_all', 'excluded_supertypes')


@dataclass(frozen=True, slots=True)
class EntryTypeScope:
    required: frozenset[str] = frozenset()
    excluded: frozenset[str] = frozenset()

    def intersect(self, other: EntryTypeScope) -> EntryTypeScope | None:
        required = self.required | other.required
        excluded = self.excluded | other.excluded
        return None if required & excluded else EntryTypeScope(required, excluded)

    def complement(self) -> tuple[EntryTypeScope, ...]:
        # The first unsatisfied atom partitions a conjunction's complement.
        prefix = EntryTypeScope()
        result = []
        for tag, positive in sorted([(tag, True) for tag in self.required]
                                    + [(tag, False) for tag in self.excluded]):
            yes = EntryTypeScope(frozenset({tag}))
            no = EntryTypeScope(excluded=frozenset({tag}))
            result.append(prefix.intersect(no if positive else yes))
            prefix = prefix.intersect(yes if positive else no)
        return tuple(result)


def entry_type_scopes(queries: tuple[ObjectQuerySpec, ...]) -> tuple[EntryTypeScope, ...]:
    """Lower a bounded flat type union without duplicate replacement options."""
    if (not isinstance(queries, tuple) or not 1 <= len(queries) <= 4
            or any(not isinstance(query, ObjectQuerySpec) for query in queries)
            or len(set(queries)) != len(queries)):
        raise ValueError('Entry subjects require one through four distinct typed queries')
    base = ObjectQuerySpec(zones=('battlefield',))
    clauses = []
    for leaf in queries:
        if leaf.any_of or replace(leaf, **{key: () for key in _TYPE_FIELDS}) != base:
            raise ValueError('Entry unions require flat type-only branches')
        if sum(len(getattr(leaf, key)) for key in _TYPE_FIELDS) > 16:
            raise ValueError('Entry subject type vocabulary exceeds its fixed bound')
        required = frozenset((*leaf.types_all, *leaf.subtypes_all, *leaf.supertypes_all))
        excluded = frozenset((*leaf.excluded_types, *leaf.excluded_subtypes, *leaf.excluded_supertypes))
        choices = [values for values in (leaf.types_any, leaf.subtypes_any) if values]
        for selected in product(*choices):
            scope = EntryTypeScope(required | frozenset(selected), excluded)
            if scope.required & scope.excluded:
                raise ValueError('Entry subjects cannot require and exclude the same type')
            clauses.append(scope)
            if len(clauses) > 128:
                raise ValueError('Entry subject clause expansion exceeds its fixed bound')
    result = []
    for clause in clauses:
        remaining = [clause]
        for earlier in result:
            remaining = [intersection for value in remaining for part in earlier.complement()
                         if (intersection := value.intersect(part)) is not None]
            if len(remaining) + len(result) > 128:
                raise ValueError('Entry subject scope expansion exceeds its fixed bound')
        result.extend(remaining)
    return tuple(result)
