"""Project an entrant's paid time-counter type condition through replacement."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .model import ReplacementEffectError, ReplaceableEvent


ENTRY_TIME_COUNTER_TYPES_FIELD = "_entry_time_counter_types"


def _canonical_types(value) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or any(type(tag) is not str or not tag or tag != tag.casefold() for tag in value)
        or tuple(value) != tuple(sorted(set(value)))
    ):
        raise ReplacementEffectError("Prospective types require sorted canonical tags")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class EntryTimeCounterTypes:
    without_counter: tuple[str, ...]
    with_counter: tuple[str, ...]

    def __post_init__(self) -> None:
        before = _canonical_types(self.without_counter)
        after = _canonical_types(self.with_counter)
        if "creature" not in before or "creature" in after or not set(after) < set(before):
            raise ReplacementEffectError("The time-counter preview must remove the creature type")
        object.__setattr__(self, "without_counter", before)
        object.__setattr__(self, "with_counter", after)

    def to_dict(self) -> dict[str, list[str]]:
        return {"without_counter": list(self.without_counter), "with_counter": list(self.with_counter)}

    @classmethod
    def from_dict(cls, raw) -> "EntryTimeCounterTypes":
        if not isinstance(raw, Mapping) or set(raw) != {"without_counter", "with_counter"}:
            raise ReplacementEffectError("Prospective time-counter types have unexpected fields")
        return cls(_canonical_types(raw["without_counter"]), _canonical_types(raw["with_counter"]))


def entry_time_counter_child(event: ReplaceableEvent, child: ReplaceableEvent) -> bool:
    return (
        event.affected_object is not None
        and child.kind == "counter.place"
        and child.affected_object is not None
        and child.affected_object.object_id == event.affected_object.object_id
        and child.payload.get("follows_zone_destination") is True
        and child.payload.get("counter_name") == "time"
    )


def project_entry_time_counter_types(event: ReplaceableEvent) -> ReplaceableEvent:
    """Update the subject and its counter children from the current paid entry."""
    if (
        event.kind != "zone.change"
        or ENTRY_TIME_COUNTER_TYPES_FIELD not in event.payload
        or event.payload.get("destination") != "battlefield"
        or event.payload.get("entry_copy") is not None
    ):
        return event
    profile = EntryTimeCounterTypes.from_dict(event.payload[ENTRY_TIME_COUNTER_TYPES_FIELD])
    has_counter = any(
        entry_time_counter_child(event, child) and child.payload["amount"] > 0
        for child in event.children
    )
    current = tuple(event.payload["object_types"])
    extra_types = set(current) - set(profile.without_counter)
    selected = profile.with_counter if has_counter else profile.without_counter
    types = tuple(sorted(set(selected) | extra_types))

    def retarget(child: ReplaceableEvent) -> ReplaceableEvent:
        family = tuple(retarget(nested) for nested in child.children) if child.children else child.children
        if (
            child.kind == "counter.place"
            and child.affected_object is not None
            and event.affected_object is not None
            and child.affected_object.object_id == event.affected_object.object_id
            and child.payload.get("follows_zone_destination") is True
            and tuple(child.payload["target_types"]) != types
        ):
            return replace(child, children=family, payload={**child.payload, "target_types": types})
        return replace(child, children=family) if family != child.children else child

    children = tuple(retarget(child) for child in event.children)
    if current == types and children == event.children:
        return event
    return replace(event, payload={**event.payload, "object_types": types}, children=children)
