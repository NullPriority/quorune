"""Count the largest group of named objects with no common rules names."""

from collections import defaultdict
from collections.abc import Iterable
from functools import cache


def distinct_public_name_count(names_by_object: Iterable[Iterable[str]]) -> int:
    """Each selected object contributes once, even if it has multiple names."""
    groups = frozenset(frozenset(names) for names in names_by_object)
    single_names = {next(iter(group)) for group in groups if len(group) == 1}
    # A group that shares a singleton name can always be replaced by that
    # singleton without reducing the count or adding a conflict.
    remaining = frozenset(group for group in groups if len(group) > 1 and group.isdisjoint(single_names))
    if not remaining:
        return len(single_names)
    return len(single_names) + _largest_disjoint_group(remaining)


def _largest_disjoint_group(groups: frozenset[frozenset[str]]) -> int:
    @cache
    def count(available: frozenset[frozenset[str]]) -> int:
        by_name = defaultdict(set)
        for group in available:
            for name in group:
                by_name[name].add(group)
        overlapping = [(name, owners) for name, owners in by_name.items() if len(owners) > 1]
        if not overlapping:
            return len(available)
        _, owners = max(overlapping, key=lambda pair: (len(pair[1]), pair[0]))
        component = set(owners)
        adjacent = list(owners)
        while adjacent:
            group = adjacent.pop()
            for name in group:
                neighbors = by_name[name] - component
                component.update(neighbors)
                adjacent.extend(neighbors)
        if len(component) < len(available):
            return count(frozenset(component)) + count(available - component)
        best = count(available - owners)
        for selected in owners:
            best = max(best, 1 + count(frozenset(group for group in available if group.isdisjoint(selected))))
        return best

    return count(groups)
