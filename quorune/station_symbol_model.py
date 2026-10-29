"""Immutable CR721 Station-symbol thresholds and compiled child identities."""
from dataclasses import dataclass
from collections.abc import Mapping

STATION_SYMBOL_CAPABILITY = 'continuous.characteristics.station_symbols'
STATION_SYMBOL_HANDLER = 'continuous.characteristics.station-symbols.v1'
STATION_SYMBOL_MECHANIC = 'cr-721-station-cards'


def _strings(values, field):
    if (not isinstance(values, tuple) or any(type(v) is not str or not v or v != v.strip() for v in values)
            or values != tuple(sorted(set(values), key=str.casefold))
            or len({v.casefold() for v in values}) != len(values)):
        raise ValueError(f'Station {field} require canonical distinct strings')


@dataclass(frozen=True, slots=True)
class StationSymbolSpec:
    minimum: int
    power: int | None = None
    toughness: int | None = None
    keywords: tuple[str, ...] = ()
    semantic_keys: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.minimum) is not int or self.minimum < 1:
            raise ValueError('Station thresholds require positive integers')
        if (self.power is None) != (self.toughness is None) or any(
                value is not None and (type(value) is not int or value < 0) for value in (self.power, self.toughness)):
            raise ValueError('Station P/T requires one nonnegative integral pair or absence')
        _strings(self.keywords, 'keywords')
        _strings(self.semantic_keys, 'component identities')

    def active(self, counters):
        if not isinstance(counters, Mapping):
            raise ValueError('Station applicability requires counter facts')
        value = counters.get('charge', 0)
        if type(value) is not int or value < 0:
            raise ValueError('Station charge counters require a nonnegative integer')
        return value >= self.minimum

    def to_dict(self):
        return {'minimum': self.minimum, 'power': self.power, 'toughness': self.toughness,
                'keywords': list(self.keywords), 'semantic_keys': list(self.semantic_keys)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'minimum', 'power', 'toughness', 'keywords', 'semantic_keys'}:
            raise ValueError('Station symbols have a closed schema')
        if not isinstance(value['keywords'], list) or not isinstance(value['semantic_keys'], list):
            raise ValueError('Station symbol members require arrays')
        return cls(value['minimum'], value['power'], value['toughness'], tuple(value['keywords']), tuple(value['semantic_keys']))


@dataclass(frozen=True, slots=True)
class StationSymbolsSpec:
    symbols: tuple[StationSymbolSpec, ...]

    def __post_init__(self):
        if (not isinstance(self.symbols, tuple) or not 1 <= len(self.symbols) <= 2
                or any(not isinstance(s, StationSymbolSpec) for s in self.symbols)
                or tuple(s.minimum for s in self.symbols) != tuple(sorted({s.minimum for s in self.symbols}))):
            raise ValueError('Station cards require one or two increasing distinct symbols')
        if any(s.power is not None for s in self.symbols[:-1]):
            raise ValueError('This representation places its single P/T box in the final striation')
        keys = [key for symbol in self.symbols for key in symbol.semantic_keys]
        if len(keys) != len(set(keys)):
            raise ValueError('One component cannot belong to multiple Station symbols')

    def to_dict(self):
        return {'symbols': [symbol.to_dict() for symbol in self.symbols]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'symbols'} or not isinstance(value['symbols'], list):
            raise ValueError('Station symbol sets have a closed schema')
        return cls(tuple(StationSymbolSpec.from_dict(symbol) for symbol in value['symbols']))

    def descriptor(self):
        return {'handler_id': STATION_SYMBOL_HANDLER, 'schema_version': 1,
                'event': 'characteristics.evaluate', **self.to_dict()}
