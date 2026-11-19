"""Actual exiled card identities and immutable running mana-value total."""
from collections.abc import Mapping
from dataclasses import dataclass
from .rules.library_selection import LibrarySelectionObjectIdentity

TOTAL_MANA_EXILE_SCOPE='controller_total_mana_value'
TOTAL_MANA_EXILE_CAPABILITY='library.exile_until_total_mana_value.cast_subset'
TOTAL_MANA_EXILE_MECHANIC='exile-until-total-mana-value-cast'


@dataclass(frozen=True, slots=True)
class ExiledCardManaValue:
    identity: LibrarySelectionObjectIdentity
    mana_value: int

    def __post_init__(self):
        if (not isinstance(self.identity,LibrarySelectionObjectIdentity)
                or type(self.mana_value) is not int or self.mana_value<0):
            raise ValueError('Mana-value scanning requires one actual exile incarnation and nonnegative integral value')

    def to_dict(self):
        return {'identity':self.identity.to_dict(),'mana_value':self.mana_value}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'identity','mana_value'}:
            raise ValueError('Exiled mana-value entry has a closed schema')
        return cls(LibrarySelectionObjectIdentity.from_dict(value['identity']),value['mana_value'])


@dataclass(frozen=True, slots=True)
class ManaValueExileProgress:
    minimum_total: int
    cards: tuple[ExiledCardManaValue,...]=()
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.minimum_total) is not int or self.minimum_total<=0
                or not isinstance(self.cards,tuple) or any(not isinstance(c,ExiledCardManaValue) for c in self.cards)
                or len({c.identity.object_id for c in self.cards})!=len(self.cards)):
            raise ValueError('Total mana-value scan requires unique ordered actual exile results and a positive threshold')
        # A valid instruction stops at the first satisfying card.
        if self.cards and sum(c.mana_value for c in self.cards[:-1])>=self.minimum_total:
            raise ValueError('Mana-value scanning continued after reaching its printed total')

    @property
    def total(self):return sum(c.mana_value for c in self.cards)

    @property
    def complete(self):return self.total>=self.minimum_total

    def append(self,identity,mana_value):
        if self.complete:raise ValueError('A completed mana-value scan cannot consume another card')
        return ManaValueExileProgress(self.minimum_total,(*self.cards,ExiledCardManaValue(identity,mana_value)))

    def to_dict(self):
        return {'schema_version':1,'minimum_total':self.minimum_total,'cards':[c.to_dict() for c in self.cards]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','minimum_total','cards'}
                or not isinstance(value['cards'],(list,tuple))):
            raise ValueError('Mana-value scan progress has a closed versioned schema')
        return cls(value['minimum_total'],tuple(ExiledCardManaValue.from_dict(c) for c in value['cards']),value['schema_version'])
