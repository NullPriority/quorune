"""Typed knowledge intents and choice admission; reveal mutation stays at its owner."""
from dataclasses import dataclass
from ..replacement.immutable import FrozenMap


@dataclass(frozen=True,slots=True)
class RevealColoredHandCardsIntent:
    actor: str
    source_ref: str
    color: str | None
    snapshots: tuple[FrozenMap,...]

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or type(self.source_ref) is not str
                or not self.source_ref or self.color is not None and self.color not in tuple('WUBRG')):
            raise ValueError('Colored hand reveal needs its controller, source and color')
        rows=tuple(FrozenMap(row) for row in self.snapshots)
        fields={'object_id','logical_object_id','ref','player','name'}
        expected = fields | {"mana_value"} if self.color is None else fields
        if (any(set(row)!=expected or any(type(row[k]) is not str or not row[k] for k in fields)
                or row['player']!=self.actor for row in rows)
                or len({row['object_id'] for row in rows})!=len(rows)
                or len({row['ref'] for row in rows})!=len(rows)):
            raise ValueError('Colored reveal snapshots require distinct original hand identities')
        if self.color is None and (len(rows) > 5 or any(
            type(row["mana_value"]) is not int or row["mana_value"] < 0 for row in rows
        )):
            raise ValueError("Nonland reveal requires at most five exact mana values")
        object.__setattr__(self,'snapshots',rows)
