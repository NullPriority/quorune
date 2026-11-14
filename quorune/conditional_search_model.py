"""Closed revealed single-card searches with a reached placement decision."""
from collections.abc import Mapping
from dataclasses import dataclass
from .library_search_model import FixedCountedLibrarySearchTemplate,counted_search_selector_is_closed
from .replacement.immutable import FrozenMap,thaw_value
from .continuous_conditions import FixedPublicStateConditionSpec
from .resolution_conditions import resolution_condition_is_closed

CONDITIONAL_SEARCH_CAPABILITY='library.search.conditional_placement'
CONDITIONAL_SEARCH_MECHANIC='conditional-library-search-placement'


@dataclass(frozen=True,slots=True)
class ConditionalSearchSpec:
    search: FixedCountedLibrarySearchTemplate
    placement: FrozenMap

    def __post_init__(self):
        s=self.search;p=self.placement
        if (not isinstance(s,FixedCountedLibrarySearchTemplate) or s.count!=1 or s.optional_count or s.disjunctive
                or s.destination!='hand' or not s.reveal or s.enters_tapped
                or not counted_search_selector_is_closed(s.selector,destination='battlefield')):
            raise ValueError('Conditional search requires one revealed permanent with hand fallback')
        if not isinstance(p,Mapping) or type(p.get('enters_tapped')) is not bool:
            raise ValueError('Conditional search placement fields are malformed')
        kind=p.get('kind')
        if kind=='public_state':
            if set(p)!={'kind','condition','enters_tapped'}:raise ValueError('Conditional search public predicate fields are closed')
            condition=FixedPublicStateConditionSpec.from_dict(p['condition'])
            if not resolution_condition_is_closed(condition):raise ValueError('Conditional search predicate needs unavailable source bindings')
        elif kind=='mana_value_at_most':
            if set(p)!={'kind','maximum','enters_tapped'} or type(p['maximum']) is not int or p['maximum']<0:
                raise ValueError('Conditional search needs one nonnegative mana-value bound')
        elif kind=='card_type':
            if set(p)!={'kind','card_type','enters_tapped'} or p['card_type'] not in {'land','creature','artifact','enchantment','battle','planeswalker'}:
                raise ValueError('Conditional search card type is unsupported')
        else:raise ValueError('Conditional search placement predicate is unsupported')
        object.__setattr__(self,'placement',FrozenMap(p))

    def effect(self):
        return {**self.search.effect(),'schema_version':4,'conditional_placement':thaw_value(self.placement)}

    @classmethod
    def from_effect(cls,effect):
        if not isinstance(effect,Mapping) or type(effect.get('schema_version')) is not int or effect['schema_version']!=4:
            raise ValueError('Conditional search requires its explicit version')
        base=dict(thaw_value(effect));placement=base.pop('conditional_placement',None);base['schema_version']=2
        return cls(FixedCountedLibrarySearchTemplate.from_effect(base),placement)

    def capabilities(self):
        required={CONDITIONAL_SEARCH_CAPABILITY,'library.search.fixed_counted'}
        if self.placement['kind']=='public_state':
            from .characteristic_fragments import characteristic_quantity_capabilities
            from .public_history_query_model import public_history_query_dependencies
            from .public_zone_condition_model import public_zone_condition_capabilities
            raw=self.placement['condition']
            required.update(('resolution.effect.public_condition',*characteristic_quantity_capabilities(raw),
                *public_history_query_dependencies(raw),*public_zone_condition_capabilities(raw)))
        return tuple(sorted(required))
