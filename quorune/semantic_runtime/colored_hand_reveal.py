"""Typed knowledge intents and choice admission; reveal mutation stays at its owner."""
from dataclasses import dataclass
from ..replacement.immutable import FrozenMap


@dataclass(frozen=True,slots=True)
class RevealColoredHandCardsIntent:
    actor: str
    source_ref: str
    color: str
    snapshots: tuple[FrozenMap,...]

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or type(self.source_ref) is not str
                or not self.source_ref or self.color not in tuple('WUBRG')):
            raise ValueError('Colored hand reveal needs its controller, source and color')
        rows=tuple(FrozenMap(row) for row in self.snapshots)
        fields={'object_id','logical_object_id','ref','player','name'}
        if (any(set(row)!=fields or any(type(row[k]) is not str or not row[k] for k in fields)
                or row['player']!=self.actor for row in rows)
                or len({row['object_id'] for row in rows})!=len(rows)
                or len({row['ref'] for row in rows})!=len(rows)):
            raise ValueError('Colored reveal snapshots require distinct original hand identities')
        object.__setattr__(self,'snapshots',rows)
