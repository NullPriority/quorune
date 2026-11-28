from __future__ import annotations

"""Apply a captured public entry count through the ordinary counter child."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping

from ..counter_names import normalized_counter_name

if TYPE_CHECKING:
    from ..counter_removal import CounterRemoval


@dataclass(frozen=True, slots=True)
class CreateCountedEntryCounters:
    counter_name: str
    source_ref: str
    placing_player: str
    matching_objects: tuple[str, ...]
    simultaneous_departures: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Counted entry counters require their version-one operation")
        if (type(self.counter_name) is not str
                or normalized_counter_name(self.counter_name) != self.counter_name
                or type(self.source_ref) is not str or not self.source_ref
                or type(self.placing_player) is not str or not self.placing_player):
            raise ValueError("Counted entry counters require a canonical name, source and placing player")
        for ids in (self.matching_objects, self.simultaneous_departures):
            if (not isinstance(ids, (list, tuple)) or any(type(i) is not str or not i for i in ids)
                    or tuple(ids) != tuple(sorted(set(ids)))):
                raise ValueError("Counted entry counter identities must be sorted and unique")
        if not self.matching_objects or not set(self.simultaneous_departures) <= set(self.matching_objects):
            raise ValueError("Simultaneous departures must belong to the captured matching objects")
        object.__setattr__(self, "matching_objects", tuple(self.matching_objects))
        object.__setattr__(self, "simultaneous_departures", tuple(self.simultaneous_departures))

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "create_counted_entry_counters",
            "schema_version": self.schema_version,
            "counter_name": self.counter_name,
            "source_ref": self.source_ref,
            "placing_player": self.placing_player,
            "matching_objects": list(self.matching_objects),
            "simultaneous_departures": list(self.simultaneous_departures),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CreateCountedEntryCounters:
        if (not isinstance(value, Mapping) or set(value) != {
            "op", "schema_version", "counter_name", "source_ref", "placing_player",
            "matching_objects", "simultaneous_departures",
        } or value["op"] != "create_counted_entry_counters"):
            raise ValueError("Counted entry counters use one closed operation payload")
        return cls(**{key: item for key, item in value.items() if key != "op"})


ENTRY_COUNTER_TRANSFER_OPERATION = "transfer_permanent_counters_on_entry"
CHOSEN_ENTRY_COUNTER_TRANSFER_OPERATION = "transfer_chosen_permanent_counters_on_entry"
ENTRY_COUNTER_TRANSFER_RESULT = "entry_counter_transfer"


@dataclass(frozen=True, slots=True)
class TransferEntryCounters:
    source_ref: str
    placing_player: str
    removals: tuple[CounterRemoval, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        from ..counter_removal import CounterRemoval

        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.source_ref) is not str or not self.source_ref
                or type(self.placing_player) is not str or not self.placing_player
                or type(self.removals) is not tuple or not self.removals
                or any(not isinstance(row, CounterRemoval) or row.expected_zone != "battlefield"
                       or row.expected_logical_object_id is None for row in self.removals)):
            raise ValueError("Counter transfer requires version one and a captured permanent counter inventory")
        keys = tuple(row.key for row in self.removals)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Counter transfer inventory requires distinct sorted permanent counters")

    @property
    def amount(self) -> int:
        return sum(row.amount for row in self.removals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": ENTRY_COUNTER_TRANSFER_OPERATION,
            "schema_version": self.schema_version,
            "source_ref": self.source_ref,
            "placing_player": self.placing_player,
            "removals": _removal_rows(self.removals),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TransferEntryCounters:
        if (not isinstance(value, Mapping)
                or set(value) != {"op", "schema_version", "source_ref", "placing_player", "removals"}
                or value["op"] != ENTRY_COUNTER_TRANSFER_OPERATION
                or not isinstance(value["removals"], (list, tuple))):
            raise ValueError("Counter transfer operation requires its closed versioned inventory")
        return cls(value["source_ref"], value["placing_player"], _parse_removals(value["removals"]), value["schema_version"])


def _removal_rows(removals):
    return [
        {"object_id": row.object_id, "logical_object_id": row.expected_logical_object_id,
         "counter_name": row.counter_name, "amount": row.amount}
        for row in removals
    ]


def _parse_removals(rows):
    from ..counter_removal import CounterRemoval

    if not isinstance(rows, (list, tuple)):
        raise ValueError("Counter transfer requires a captured inventory")
    removals = []
    for row in rows:
        if (not isinstance(row, Mapping) or set(row) != {"object_id", "logical_object_id", "counter_name", "amount"}
                or type(row["counter_name"]) is not str
                or normalized_counter_name(row["counter_name"]) != row["counter_name"]):
            raise ValueError("Counter transfer requires an exact canonical counter row")
        removals.append(CounterRemoval(
            row["object_id"], row["counter_name"], row["amount"],
            expected_logical_object_id=row["logical_object_id"],
        ))
    return tuple(removals)


@dataclass(frozen=True, slots=True)
class ChosenTransferEntryCounters:
    source_ref: str
    placing_player: str
    candidates: tuple[tuple[str, str], ...]
    removals: tuple[CounterRemoval, ...]
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.source_ref) is not str or not self.source_ref
                or type(self.placing_player) is not str or not self.placing_player
                or type(self.candidates) is not tuple
                or any(type(candidate) is not tuple or len(candidate) != 2
                       or any(type(field) is not str or not field for field in candidate)
                       for candidate in self.candidates)):
            raise ValueError("Chosen counter transfer requires its version, source and public candidates")
        references, objects = zip(*self.candidates) if self.candidates else ((), ())
        if (references != tuple(sorted(set(references))) or len(objects) != len(set(objects))
                or type(self.removals) is not tuple):
            raise ValueError("Chosen counter-transfer candidates must have distinct sorted references")
        if self.removals:
            TransferEntryCounters(self.source_ref, self.placing_player, self.removals)
            if not {row.object_id for row in self.removals} <= set(objects):
                raise ValueError("Counter-removal inventory must belong to the offered permanents")

    @property
    def amount(self):
        return 2 * sum(row.amount for row in self.removals)

    def eligible_refs(self, event):
        scope = event.entry_scope
        objects = {object_id for _, object_id in self.candidates}
        eligible = objects if scope is None else set(scope.eligible_zone_choices(objects))
        return tuple(ref for ref, object_id in self.candidates if object_id in eligible)

    def select(self, event, references):
        refs = () if references is None else references
        if not isinstance(refs, (list, tuple)) or any(type(ref) is not str or not ref for ref in refs):
            raise ValueError("Counter transfer requires a list of public permanent references")
        selected = set(refs)
        if len(selected) != len(refs) or not selected <= set(self.eligible_refs(event)):
            raise ValueError("Counter transfer requires distinct offered public permanents")
        candidates = tuple(row for row in self.candidates if row[0] in selected)
        objects = {object_id for _, object_id in candidates}
        return replace(self, candidates=candidates, removals=tuple(row for row in self.removals if row.object_id in objects))

    def to_dict(self):
        return {
            "op": CHOSEN_ENTRY_COUNTER_TRANSFER_OPERATION,
            "schema_version": self.schema_version,
            "source_ref": self.source_ref,
            "placing_player": self.placing_player,
            "candidates": [list(row) for row in self.candidates],
            "removals": _removal_rows(self.removals),
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"op", "schema_version", "source_ref", "placing_player", "candidates", "removals"}
                or value["op"] != CHOSEN_ENTRY_COUNTER_TRANSFER_OPERATION
                or not isinstance(value["candidates"], (list, tuple))
                or any(not isinstance(row, (list, tuple)) for row in value["candidates"])):
            raise ValueError("Chosen counter transfer requires its exact versioned candidates")
        return cls(value["source_ref"], value["placing_player"], tuple(map(tuple, value["candidates"])),
                   _parse_removals(value["removals"]), value["schema_version"])


ENTRY_COUNTER_TRANSFER_OPERATIONS = (TransferEntryCounters, ChosenTransferEntryCounters)


def entry_counter_transfer_receipt(value):
    if isinstance(value, Mapping) and value.get("op") == CHOSEN_ENTRY_COUNTER_TRANSFER_OPERATION:
        return ChosenTransferEntryCounters.from_dict(value)
    return TransferEntryCounters.from_dict(value)


def apply_transferred_entry_counters(event, payload, children, operation, *, effect_id):
    from .entry_counter_application import add_entry_counters
    from .model import ReplacementEffectError
    from .operations import AddAffectedObjectCounter

    if (event.kind != "zone.change" or event.affected_object is None
            or payload.get("destination") != "battlefield" or payload.get("origin") == "battlefield"
            or payload.get("object_ref") != operation.source_ref
            or payload.get("destination_controller") != operation.placing_player
            or ENTRY_COUNTER_TRANSFER_RESULT in payload):
        raise ReplacementEffectError("Counter transfer requires one captured original entering permanent")
    if not operation.removals:
        return
    add_entry_counters(
        event, payload, children,
        AddAffectedObjectCounter(counter_name="+1/+1", amount=operation.amount,
                                 placing_player=operation.placing_player, source_ref=operation.source_ref),
        effect_id=effect_id,
    )
    payload[ENTRY_COUNTER_TRANSFER_RESULT] = operation.to_dict()


def apply_counted_entry_counters(event, payload, children, entry_scope, operation, *, effect_id):
    from .application import _create_affected_object_counter_event
    from .model import ReplacementEffectError
    from .operations import CreateAffectedObjectCounter

    if (event.kind != "zone.change" or event.affected_object is None
            or payload.get("destination") != "battlefield"
            or payload.get("object_ref") != operation.source_ref
            or payload.get("destination_controller") != operation.placing_player):
        raise ReplacementEffectError("Counted entry counters require their captured battlefield entry")

    reserved = set(entry_scope.reserved_zone_changes) if entry_scope is not None else set()
    earlier_departures = reserved.difference(operation.simultaneous_departures)
    amount = len(set(operation.matching_objects).difference(earlier_departures))
    if amount:
        _create_affected_object_counter_event(event, payload, children, CreateAffectedObjectCounter(
            counter_name=operation.counter_name, amount=amount, placing_player=operation.placing_player,
            source_ref=operation.source_ref,
        ), effect_id=effect_id)
