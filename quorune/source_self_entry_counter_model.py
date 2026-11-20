"""Fixed counters carried by an activated source's graveyard entry."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from .entry_counter_model import EffectEntryCounter

FIELD = 'self_entry_counter'
CAPABILITY = 'zone.self_move.fixed_entry_counter'
MECHANIC = 'source-self-fixed-entry-counter'
DEPENDENCIES = (CAPABILITY, 'zone.self_move.activated', 'counter.producer.effect_entry')
_AMOUNTS = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}


@dataclass(frozen=True, slots=True)
class FixedSelfEntryCounter:
    amount: int

    def __post_init__(self):
        if type(self.amount) is not int or not 1 <= self.amount <= 10:
            raise ValueError('Source entry counters require a fixed amount from one through ten')

    def to_dict(self):
        return {'version': 1, 'counter_name': '+1/+1', 'amount': self.amount}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'version', 'counter_name', 'amount'}
                or type(value['version']) is not int or value['version'] != 1
                or value['counter_name'] != '+1/+1'):
            raise ValueError('Source entry counters require their complete versioned shape')
        return cls(value['amount'])

    def materialize(self, *, actor, source):
        return EffectEntryCounter('+1/+1', self.amount, actor, source, '122.6a')


def compile_source_self_entry_counter(text):
    match = re.fullmatch(
        r'Return this card from your graveyard to the battlefield(?P<tapped> tapped)? '
        r'with (?P<amount>a|one|two|three|four|five|six|seven|eight|nine|ten) '
        r'\+1/\+1 (?P<noun>counter|counters) on it\.', text)
    if match is None:
        return None
    amount = _AMOUNTS[match['amount']]
    if (match['noun'] == 'counter') != (amount == 1):
        return None
    return bool(match['tapped']), FixedSelfEntryCounter(amount)


def source_self_entry_counter_from_effect(effect):
    fields = {'op', 'origin', 'destination', 'tapped', 'source_form', FIELD}
    if (not isinstance(effect, Mapping)
            or set(effect) - {'_replacement_selections'} != fields
            or effect['op'] != 'self_zone_move'
            or (effect['origin'], effect['destination'], effect['source_form'])
                != ('graveyard', 'battlefield', 'card')
            or type(effect['tapped']) is not bool):
        raise ValueError('Source entry counters require one sealed graveyard-to-battlefield move')
    return FixedSelfEntryCounter.from_dict(effect[FIELD])


def source_self_entry_counter_payload_dependencies(value):
    if FIELD not in value:
        return ()
    source_self_entry_counter_from_effect(value)
    return DEPENDENCIES
