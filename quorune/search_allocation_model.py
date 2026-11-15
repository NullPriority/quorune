"""Ordered found-card allocations over one ordinary fixed library search."""
from collections.abc import Mapping
from dataclasses import dataclass

from .library_search_model import (
    FixedCountedLibrarySearchTemplate, SEARCH_ALTERNATIVES,
    FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID,
    DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID,
)
from .replacement.immutable import thaw_value

SEARCH_ALLOCATION_CAPABILITY = 'library.search.ordered_destination_allocation'
SEARCH_ALLOCATION_MECHANIC = 'ordered-search-destination-allocation'


def _land_leaf(leaf, *, basic_only):
    if set(leaf) - {'types', 'supertypes', 'subtypes_any'} or tuple(leaf.get('types', ())) != ('land',):return False
    basic = tuple(leaf.get('supertypes', ())) == ('basic',)
    subtypes = set(leaf.get('subtypes_any', ()))
    if basic:return subtypes <= {'plains', 'island', 'swamp', 'mountain', 'forest'}
    return not basic_only and not leaf.get('supertypes') and subtypes == {'gate'}


@dataclass(frozen=True, slots=True)
class SearchAllocationSpec:
    search: FixedCountedLibrarySearchTemplate
    first_count: int
    first_destination: str
    remainder_destination: str
    first_tapped: bool = False

    def __post_init__(self):
        base = self.search
        if (not isinstance(base, FixedCountedLibrarySearchTemplate) or not 2 <= base.count <= 3
                or base.destination != 'hand' or base.enters_tapped or type(self.first_count) is not int
                or not 1 <= self.first_count <= min(2, base.count) or type(self.first_tapped) is not bool):
            raise ValueError('Search allocation requires its fixed found-card count and first group')
        if (self.first_destination, self.remainder_destination) == ('battlefield', 'hand'):
            leaves = base.selector[SEARCH_ALTERNATIVES] if base.disjunctive else (base.selector,)
            if not all(_land_leaf(leaf, basic_only=self.first_count > 1) for leaf in leaves):
                raise ValueError('Battlefield search allocation requires basic lands or a single Gate entry')
        elif (self.first_destination, self.remainder_destination) == ('hand', 'graveyard'):
            if self.first_count != 1 or base.count != 2 or self.first_tapped:
                raise ValueError('Hand/graveyard search requires its one/one allocation')
        else:raise ValueError('Search allocation destinations are unsupported')

    def effect(self):
        return {**self.search.effect(), 'schema_version': 5, 'allocation': {
            'first_count': self.first_count, 'first_destination': self.first_destination,
            'remainder_destination': self.remainder_destination, 'first_tapped': self.first_tapped}}

    @classmethod
    def from_effect(cls, effect):
        if not isinstance(effect, Mapping) or type(effect.get('schema_version')) is not int or effect['schema_version'] != 5:
            raise ValueError('Search allocation requires its explicit version')
        base = dict(thaw_value(effect));allocation = base.pop('allocation', None)
        if not isinstance(allocation, Mapping) or set(allocation) != {'first_count', 'first_destination', 'remainder_destination', 'first_tapped'}:
            raise ValueError('Search allocation fields are closed')
        base['schema_version'] = 3 if SEARCH_ALTERNATIVES in base.get('selector', {}) else 2
        return cls(FixedCountedLibrarySearchTemplate.from_effect(base), **allocation)

    def capabilities(self):
        return tuple(sorted({SEARCH_ALLOCATION_CAPABILITY, 'library.search.fixed_counted',
            *((DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID,) if self.search.disjunctive else ()),
            *(('zone.entry.tapped_state',) if self.first_tapped else ())}))

    def compiled(self):
        mechanics = (FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, SEARCH_ALLOCATION_MECHANIC,
            *((DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID,) if self.search.disjunctive else ()))
        return 'ordered-search-destination-allocation-v1', (self.effect(),), None, mechanics

    def destinations(self, selected):
        return tuple(self.first_destination if i < self.first_count else self.remainder_destination for i, _ in enumerate(selected))

    def choice_metadata(self):
        first = 'the battlefield' + (' tapped' if self.first_tapped else '') if self.first_destination == 'battlefield' else 'your hand'
        if self.first_count == self.search.count:return {'label': f'Find cards to put onto {first}.'}
        rest = 'your hand' if self.remainder_destination == 'hand' else 'your graveyard'
        return {'order': 'destination_groups', 'label': f'Find cards in order: first {self.first_count} to {first}; the rest to {rest}.'}
