from __future__ import annotations

"""Immutable relative-power target restrictions.

The model pins the public last-known power of a source while allowing the
targeting adapter to supply that source's current power when the same logical
object is still present.  It contains no game-state access or Oracle text.
"""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence


class RelativePowerTargetError(ValueError):
    """A relative-power target condition is malformed."""


CREATURE_POWER_PREDICATE = "power_less_than_source"
MANA_VALUE_LT_POWER = "mana_value_less_than_source_power"
MANA_VALUE_LE_POWER = "mana_value_at_most_source_power"
MANA_VALUE_POWER_PREDICATES = frozenset({MANA_VALUE_LT_POWER, MANA_VALUE_LE_POWER})
RELATIVE_POWER_TARGET_PREDICATES = MANA_VALUE_POWER_PREDICATES | {CREATURE_POWER_PREDICATE}
MANA_VALUE_POWER_CAPABILITY = "target.card.mana_value_source_power"


class RelativePowerDepartureHost(Protocol):
    state: Any

    def _numeric_stat(self, object_id: str, stat: str) -> int: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _identity(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise RelativePowerTargetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class RelativePowerSourceSnapshot:
    object_id: str
    logical_object_id: str
    reference: str
    last_known_power: int | None

    def __post_init__(self) -> None:
        _identity(self.object_id, field="Relative-power source object ID")
        _identity(
            self.logical_object_id,
            field="Relative-power source logical identity",
        )
        _identity(self.reference, field="Relative-power source reference")
        if self.last_known_power is not None and type(
            self.last_known_power
        ) is not int:
            raise RelativePowerTargetError(
                "Relative-power source power must be an exact integer or null"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "reference": self.reference,
            "last_known_power": self.last_known_power,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "RelativePowerSourceSnapshot":
        expected = {
            "object_id",
            "logical_object_id",
            "reference",
            "last_known_power",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RelativePowerTargetError(
                "Relative-power source snapshots have a closed schema"
            )
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class RelativePowerDepartureSnapshot:
    object_id: str
    logical_object_id: str
    last_known_power: int | None
    phased_out: bool = False

    def __post_init__(self) -> None:
        if type(self.phased_out) is not bool:
            raise RelativePowerTargetError("Departure phasing must be a boolean")
        _identity(self.object_id, field="Departing source object ID")
        _identity(
            self.logical_object_id,
            field="Departing source logical identity",
        )
        if self.last_known_power is not None and type(
            self.last_known_power
        ) is not int:
            raise RelativePowerTargetError(
                "Departing source power must be an exact integer or null"
            )


@dataclass(frozen=True, slots=True)
class RelativePowerTargetCondition:
    source: RelativePowerSourceSnapshot
    schema_version: int = 1
    kind: str = CREATURE_POWER_PREDICATE

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise RelativePowerTargetError(
                "Unsupported relative-power target schema version"
            )
        if type(self.kind) is not str or self.kind not in RELATIVE_POWER_TARGET_PREDICATES:
            raise RelativePowerTargetError(
                "Unsupported relative-power target condition"
            )
        if not isinstance(self.source, RelativePowerSourceSnapshot):
            raise RelativePowerTargetError(
                "Relative-power conditions require a typed source snapshot"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "RelativePowerTargetCondition":
        expected = {"schema_version", "kind", "source"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise RelativePowerTargetError(
                "Relative-power target conditions have a closed schema"
            )
        source = value["source"]
        if not isinstance(source, Mapping):
            raise RelativePowerTargetError(
                "Relative-power target source must be an object"
            )
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            source=RelativePowerSourceSnapshot.from_dict(source),
        )

    def permits(
        self,
        *,
        target_power: int,
        current_source_power: int | None,
        use_last_known: bool = False,
    ) -> bool:
        if self.kind != CREATURE_POWER_PREDICATE or type(target_power) is not int:
            raise RelativePowerTargetError("Relative-power comparisons require exact integer power")
        source_power = self._source_power(current_source_power, use_last_known)
        return source_power is not None and target_power < source_power

    def permits_mana_value(self, *, mana_value, current_source_power, use_last_known):
        if self.kind not in MANA_VALUE_POWER_PREDICATES or (
            type(mana_value) not in {int, float} or not 0 <= mana_value < float("inf")
        ):
            raise RelativePowerTargetError("A relative mana-value comparison needs a finite mana value")
        # CR 107.2 uses zero for the power of a known source without power.
        source_power = self._source_power(current_source_power, use_last_known)
        if source_power is None:
            source_power = 0
        if self.kind == MANA_VALUE_LT_POWER:
            return mana_value < source_power
        return mana_value <= source_power

    def _source_power(self, current, use_last_known):
        if current is not None and type(current) is not int:
            raise RelativePowerTargetError("Relative-power comparisons require exact integer power")
        if type(use_last_known) is not bool:
            raise RelativePowerTargetError("Relative-power LKI selection must be a boolean")
        return self.source.last_known_power if use_last_known else current


def bound_relative_power_condition(predicate, value):
    condition = RelativePowerTargetCondition.from_dict(value)
    if condition.kind != predicate:
        raise RelativePowerTargetError("Relative-power target predicate changed")
    return condition


def _trigger_source_power(host, source, death_context, source_characteristics):
    if death_context is None:
        return current_effective_creature_power(host, source)
    from .creature_power_damage import validate_occurrence_power_snapshot
    from .creature_power_damage_model import OCCURRENCE_POWER_DAMAGE_CONTEXT

    raw, departed = validate_occurrence_power_snapshot(
        death_context, (source_characteristics or {}).get(OCCURRENCE_POWER_DAMAGE_CONTEXT),
    )
    if (departed.object_id, departed.logical_object_id) != (source.object_id, source.logical_object_id):
        raise RelativePowerTargetError("Relative mana-value targets lost their departing source")
    return raw["power"]


def bind_relative_mana_value_trigger_schema(
    host, source, schema, *, death_context=None, source_characteristics=None,
):
    """Pin the exact battlefield source before the trigger waits for placement."""

    if not isinstance(schema, Mapping) or schema.get("predicate") not in MANA_VALUE_POWER_PREDICATES:
        return {}
    if source.zone != "battlefield" or source.phased_out or "resolution_condition" in schema:
        raise RelativePowerTargetError("Relative mana-value targets require their current source event")
    known = RelativePowerSourceSnapshot(
        source.object_id, source.logical_object_id, source.ref,
        _trigger_source_power(host, source, death_context, source_characteristics),
    )
    condition = RelativePowerTargetCondition(known, kind=schema["predicate"])
    return {"target_schema_override": {"groups": [
        {**dict(schema), "resolution_condition": condition.to_dict()},
    ]}}


def current_effective_creature_power(
    host: RelativePowerDepartureHost,
    card: Any,
) -> int | None:
    """Return current effective power, or null when the permanent is not a creature."""

    try:
        data = host._effective_card_data(card)
        if not isinstance(data, Mapping):
            raise RelativePowerTargetError(
                "Relative-power characteristics must be an object"
            )
        type_line = data.get("type_line")
        if not isinstance(type_line, str) or not type_line.strip():
            raise RelativePowerTargetError(
                "Relative-power characteristics require a type line"
            )
        parts = host._type_parts(type_line)
        if not isinstance(parts, tuple) or len(parts) != 3:
            raise RelativePowerTargetError(
                "Relative-power type parsing returned malformed data"
            )
        card_types = parts[0]
        if not isinstance(card_types, (set, frozenset)) or any(
            not isinstance(value, str) or not value.strip()
            for value in card_types
        ):
            raise RelativePowerTargetError(
                "Relative-power card types are malformed"
            )
        if "creature" not in {
            value.casefold() for value in card_types
        }:
            return None
        power = host._numeric_stat(card.object_id, "power")
    except RelativePowerTargetError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RelativePowerTargetError(
            "Relative-power characteristics could not be evaluated"
        ) from exc
    if type(power) is not int:
        raise RelativePowerTargetError(
            "Relative-power source power must be an exact integer"
        )
    return power


def _target_conditions(context, predicates):
    if not isinstance(context, Mapping):
        return
    schema = context.get("target_schema_override")
    if not isinstance(schema, Mapping):
        return
    groups = schema.get("groups")
    if not isinstance(groups, (list, tuple)):
        return
    for index, group in enumerate(groups):
        if isinstance(group, Mapping) and group.get("predicate") in predicates:
            yield index, bound_relative_power_condition(group["predicate"], group.get("resolution_condition"))


def _condition_id(condition):
    return condition.source.object_id, condition.source.logical_object_id


def relative_power_source_identities(
    stack_items: Sequence[Any], *, predicates=RELATIVE_POWER_TARGET_PREDICATES,
) -> frozenset[tuple[str, str]]:
    """Return source incarnations used by pending relative-power targets."""

    return frozenset(
        _condition_id(condition)
        for item in stack_items
        for _, condition in _target_conditions(getattr(item, "context", {}), predicates)
    )


def _departure_context(context, by_identity, predicates):
    groups = None
    for index, condition in _target_conditions(context, predicates):
        departure = by_identity.get(_condition_id(condition))
        if departure is None or (departure.phased_out and condition.kind in MANA_VALUE_POWER_PREDICATES):
            continue
        if groups is None:
            schema = context["target_schema_override"]
            groups = list(schema["groups"])
        updated_condition = replace(
            condition,
            source=replace(condition.source, last_known_power=departure.last_known_power),
        )
        groups[index] = {**dict(groups[index]), "resolution_condition": updated_condition.to_dict()}
    if groups is None:
        return None
    return {**dict(context), "target_schema_override": {**dict(schema), "groups": groups}}


def _departure_identities(departures):
    values = tuple(departures)
    if any(not isinstance(value, RelativePowerDepartureSnapshot) for value in values):
        raise RelativePowerTargetError("Relative-power departures require typed snapshots")
    by_identity = {(value.object_id, value.logical_object_id): value for value in values}
    if len(by_identity) != len(values):
        raise RelativePowerTargetError("Relative-power departure identities must be unique")
    return by_identity


def _stack_departure_updates(stack_items, by_identity, predicates):
    return [
        (item, context) for item in stack_items
        if (context := _departure_context(getattr(item, "context", {}), by_identity, predicates)) is not None
    ]


def pin_relative_power_source_departures(
    stack_items: Sequence[Any],
    departures: Sequence[RelativePowerDepartureSnapshot],
) -> int:
    """Pin immediate predeparture power into matching pending target schemas."""

    updates = _stack_departure_updates(
        stack_items, _departure_identities(departures), RELATIVE_POWER_TARGET_PREDICATES,
    )
    for item, context in updates:
        item.context = context
    return len(updates)


def _waiting_departure_updates(batches, by_identity):
    for index, batch in enumerate(batches):
        contexts = {
            item.ref: context for item in batch.items
            if (context := _departure_context(item.event_facts, by_identity, MANA_VALUE_POWER_PREDICATES)) is not None
        }
        if contexts:
            groups = tuple(replace(group, items=tuple(
                replace(item, payload={**dict(item.payload), "context": contexts[item.ref]})
                if item.ref in contexts else item
                for item in group.items
            )) for group in batch.groups)
            yield index, replace(batch, groups=groups)


def pin_host_relative_power_source_departures(
    host: RelativePowerDepartureHost,
    cards: Sequence[Any],
    *,
    error_type: type[Exception] = RelativePowerTargetError,
    phase_out: bool = False,
) -> int:
    """Capture power for exact stack and waiting sources before a transition."""

    predicates = MANA_VALUE_POWER_PREDICATES if phase_out else RELATIVE_POWER_TARGET_PREDICATES
    try:
        batches = getattr(host.state, "pending_trigger_batches", ())
        identities = relative_power_source_identities(host.state.stack, predicates=predicates) | {
            _condition_id(condition) for batch in batches for item in batch.items
            for _, condition in _target_conditions(item.event_facts, MANA_VALUE_POWER_PREDICATES)
        }
        by_identity = _departure_identities(
            RelativePowerDepartureSnapshot(
                card.object_id, card.logical_object_id, current_effective_creature_power(host, card),
                phased_out=getattr(card, "phased_out", False),
            )
            for card in cards
            if getattr(card, "zone", None) == "battlefield"
            and (card.object_id, card.logical_object_id) in identities
        )
        if not by_identity:
            return 0
        updates = _stack_departure_updates(host.state.stack, by_identity, predicates)
        pending = tuple(_waiting_departure_updates(batches, by_identity))
        from .trigger_processing import replace_pending_trigger_batch

        for item, context in updates:
            item.context = context
        for index, batch in pending:
            replace_pending_trigger_batch(host, index, batch)
        return len(updates) + len(pending)
    except RelativePowerTargetError as exc:
        if error_type is RelativePowerTargetError:
            raise
        raise error_type(str(exc)) from exc


__all__ = [
    "RelativePowerDepartureSnapshot",
    "RelativePowerDepartureHost",
    "RelativePowerSourceSnapshot",
    "RelativePowerTargetCondition",
    "RelativePowerTargetError",
    "current_effective_creature_power",
    "pin_relative_power_source_departures",
    "pin_host_relative_power_source_departures",
    "relative_power_source_identities",
]
