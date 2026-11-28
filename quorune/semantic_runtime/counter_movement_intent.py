from __future__ import annotations

"""Immutable identity-pinned CR 122.5 counter movement."""

from collections.abc import Mapping
from dataclasses import dataclass
from ..counter_names import normalized_counter_name
from ..counter_snapshot import permanent_counter_snapshot
from ..fixed_counter_movement_model import SOURCE_GIVES_COUNTERS_THEN_DRAW, SOURCE_RECEIVES_MISSING_KINDS
from ..replacement.immutable import FrozenMap


def decode_counter_movement_identity(value):
    fields = {"actor", "from_ref", "from_identity", "to_ref", "to_identity",
              "counter_name", "amount", "reason", "source_ref"}
    if isinstance(value, Mapping):
        if set(value) == fields:
            return MoveCountersIntent(**value).identity()
        set_fields = fields - {"counter_name", "amount"} | {"amounts"}
        shape = set(value)
        if (shape == set_fields
                or shape == set_fields | {"all_to_creature"} and value["all_to_creature"] is True
                or shape == set_fields | {"mode"} and value["mode"] is not None):
            return MoveCounterSetIntent(**value).identity()
    raise ValueError("Counter movement continuation identity is malformed")


@dataclass(frozen=True, slots=True)
class MoveCountersIntent:
    actor: str
    from_ref: str
    from_identity: str
    to_ref: str
    to_identity: str
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        from .intents import _freeze_replacement_selections
        for value in (self.actor, self.from_ref, self.from_identity,
                      self.to_ref, self.to_identity, self.reason):
            if type(value) is not str or not value:
                raise ValueError("Counter movement requires pinned nonempty identities")
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Counter movement requires a positive exact amount")
        if self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref):
            raise ValueError("Counter movement source reference must be nonempty")
        object.__setattr__(self, "counter_name", normalized_counter_name(self.counter_name))
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            tuple(self.replacement_selections), family="Counter movement"))

    @property
    def amounts(self) -> FrozenMap:
        return FrozenMap({self.counter_name: self.amount})

    def identity(self) -> dict[str, object]:
        return {key: getattr(self, key) for key in (
            "actor", "from_ref", "from_identity", "to_ref", "to_identity",
            "counter_name", "amount", "reason", "source_ref")}


@dataclass(frozen=True, slots=True)
class MoveCounterSetIntent:
    actor: str
    from_ref: str
    from_identity: str
    to_ref: str
    to_identity: str
    amounts: FrozenMap
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    mode: str | None = None

    all_to_creature: bool = False

    def __post_init__(self) -> None:
        from .intents import _freeze_replacement_selections
        if any(type(value) is not str or not value for value in
               (self.actor, self.from_ref, self.from_identity, self.to_ref, self.to_identity, self.reason)):
            raise ValueError("Counter movement requires pinned nonempty identities")
        if self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref):
            raise ValueError("Counter movement source reference must be nonempty")
        if type(self.all_to_creature) is not bool:
            raise ValueError("Counter movement destination mode must be explicit")
        if self.mode is not None and self.mode not in (SOURCE_RECEIVES_MISSING_KINDS, SOURCE_GIVES_COUNTERS_THEN_DRAW):
            raise ValueError("Counter set movement requires its original mode")
        amounts = permanent_counter_snapshot(self.amounts)
        if not amounts or amounts != self.amounts:
            raise ValueError("Counter movement requires a nonempty set of positive canonical amounts")
        if self.mode == SOURCE_RECEIVES_MISSING_KINDS and any(amount != 1 for amount in amounts.values()):
            raise ValueError("Movement onto the source requires at most one of each missing kind")
        object.__setattr__(self, "amounts", amounts)
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            tuple(self.replacement_selections), family="Counter movement"))

    def identity(self) -> dict[str, object]:
        return {
            "actor": self.actor, "from_ref": self.from_ref, "from_identity": self.from_identity,
            "to_ref": self.to_ref, "to_identity": self.to_identity, "amounts": dict(self.amounts),
            "reason": self.reason, "source_ref": self.source_ref,
            **({"all_to_creature": True} if self.all_to_creature else {}),
            **({"mode": self.mode} if self.mode is not None else {}),
        }


def counter_set_controllers_match(mode, actor, donor_controller, recipient_controller):
    return ((mode == SOURCE_GIVES_COUNTERS_THEN_DRAW or donor_controller == actor)
            and (mode == SOURCE_RECEIVES_MISSING_KINDS or recipient_controller == actor))


@dataclass(frozen=True, slots=True)
class CounterMovementShare:
    ref: str
    identity: str
    amount: int

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.ref, self.identity)):
            raise ValueError("Counter movement needs an original recipient identity")
        if type(self.amount) is not int or self.amount < 1:
            raise ValueError("A counter movement share must be a positive integer")

    def to_dict(self):
        return {"ref": self.ref, "identity": self.identity, "amount": self.amount}


@dataclass(frozen=True, slots=True)
class DistributeSourceCountersIntent:
    actor: str
    from_ref: str
    from_identity: str
    allocations: tuple[CounterMovementShare, ...]
    reason: str
    source_ref: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        from .intents import _freeze_replacement_selections

        for value in (self.actor, self.from_ref, self.from_identity, self.reason, self.source_ref):
            if type(value) is not str or not value:
                raise ValueError("Counter distribution requires its original source and controller")
        shares = tuple(self.allocations)
        if not shares or any(not isinstance(share, CounterMovementShare) for share in shares):
            raise ValueError("Counter distribution needs typed recipient shares")
        refs = tuple(share.ref for share in shares)
        identities = {share.identity for share in shares}
        if (refs != tuple(sorted(set(refs))) or len(identities) != len(shares)
                or self.from_ref in refs or self.from_identity in identities):
            raise ValueError("Counter distribution requires distinct other recipients")
        object.__setattr__(self, "allocations", shares)
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            tuple(self.replacement_selections), family="Counter distribution",
        ))

    @property
    def counter_name(self):
        return "+1/+1"

    @property
    def amount(self):
        return sum(share.amount for share in self.allocations)

    def identity(self):
        return {
            "actor": self.actor, "from_ref": self.from_ref, "from_identity": self.from_identity,
            "allocations": [share.to_dict() for share in self.allocations],
            "reason": self.reason, "source_ref": self.source_ref,
        }


def decode_counter_distribution_identity(value):
    fields = {"actor", "from_ref", "from_identity", "allocations", "reason", "source_ref"}
    if (not isinstance(value, Mapping) or set(value) != fields
            or not isinstance(value["allocations"], (list, tuple))):
        raise ValueError("Counter distribution continuation identity is malformed")
    shares = tuple(CounterMovementShare(**row) for row in value["allocations"])
    return DistributeSourceCountersIntent(**{**value, "allocations": shares}).identity()


CounterMovementIntent = MoveCountersIntent | MoveCounterSetIntent | DistributeSourceCountersIntent
COUNTER_MOVEMENT_INTENT_TYPES = (MoveCountersIntent, MoveCounterSetIntent, DistributeSourceCountersIntent)
