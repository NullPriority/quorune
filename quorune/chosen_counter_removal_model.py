"""A closed instruction to remove one counter kind chosen at resolution."""
from collections.abc import Mapping
from dataclasses import dataclass

CHOSEN_COUNTER_REMOVAL_OPERATION = 'remove_chosen_counter'
CHOSEN_COUNTER_REMOVAL_CAPABILITY = 'counter.removal.chosen_kind'
CHOSEN_COUNTER_REMOVAL_MECHANIC = 'chosen-counter-removal'
CHOSEN_COUNTER_REMOVAL_TEMPLATE = 'chosen-counter-removal-v1'
CHOSEN_COUNTER_REMOVAL_FIELDS = frozenset({'op', 'schema_version', 'card', 'source'})


@dataclass(frozen=True, slots=True)
class ChosenCounterRemovalSpec:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Chosen counter removal requires explicit version one')

    def instruction(self):
        return {'op': CHOSEN_COUNTER_REMOVAL_OPERATION, 'schema_version': self.schema_version,
                'card': '$target.0', 'source': '$source'}

    @classmethod
    def decode(cls, effect, *, materialized=False):
        if (not isinstance(effect, Mapping) or set(effect) != CHOSEN_COUNTER_REMOVAL_FIELDS
                or effect.get('op') != CHOSEN_COUNTER_REMOVAL_OPERATION):
            raise ValueError('Chosen counter removal fields are closed')
        cls(effect['schema_version'])
        if materialized:
            for key in ('card', 'source'):
                if effect[key] is not None and (type(effect[key]) is not str or not effect[key]
                                               or effect[key].startswith('$')):
                    raise ValueError('Chosen counter removal references must already be materialized')
        elif effect['card'] != '$target.0' or effect['source'] != '$source':
            raise ValueError('Chosen counter removal retains the original target and source')
        return effect['card'], effect['source']
