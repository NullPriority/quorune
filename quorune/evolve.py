from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


EVOLVE_EVENT_CONDITION_FIELD = "evolve_entered_creature_is_larger"
EVOLVE_ENTRANT_CONTEXT = "evolve_entrant_observation"
EVOLVE_ENTRANT_LKI_VERSION_FIELD = "evolve_entrant_lki_version"
EVOLVE_ENTRANT_LKI_VERSION = 1


class EvolveError(ValueError):
    """A typed Evolve characteristic snapshot is malformed."""


@dataclass(frozen=True, slots=True)
class EvolveCharacteristics:
    """The current characteristics CR 702.100 compares at one check."""

    is_creature: bool
    power: int
    toughness: int

    def __post_init__(self) -> None:
        if type(self.is_creature) is not bool:
            raise EvolveError("Evolve creature status must be a boolean")
        for field in ("power", "toughness"):
            if type(getattr(self, field)) is not int:
                raise EvolveError(
                    f"Evolve {field} must be an exact integer"
                )



def evolve_entrant_lki_version(value):
    if value is not None and (type(value) is not int or value != EVOLVE_ENTRANT_LKI_VERSION):
        raise EvolveError("Unsupported Evolve entrant observation version")
    return value


def evolve_entrant_lki_fields(value):
    version = evolve_entrant_lki_version(value)
    return {EVOLVE_ENTRANT_LKI_VERSION_FIELD: version} if version is not None else {}


@dataclass(frozen=True, slots=True)
class EvolveEntrantObservation:
    reference: str
    zone_change_counter: int
    last_known: EvolveCharacteristics | None = None

    def __post_init__(self):
        if type(self.reference) is not str or not self.reference:
            raise EvolveError("Evolve event requires an entered card ref")
        if type(self.zone_change_counter) is not int or self.zone_change_counter < 0:
            raise EvolveError("Evolve event requires a nonnegative zone-change counter")
        if self.last_known is not None and not isinstance(self.last_known, EvolveCharacteristics):
            raise EvolveError("Evolve entrant last-known characteristics must be typed")

    def identifies(self, card):
        return (card.ref, card.zone_change_counter) == (self.reference, self.zone_change_counter)

    def to_dict(self):
        stats = self.last_known
        return {
            "schema_version": EVOLVE_ENTRANT_LKI_VERSION,
            "reference": self.reference,
            "zone_change_counter": self.zone_change_counter,
            "last_known": None if stats is None else {
                "is_creature": stats.is_creature, "power": stats.power, "toughness": stats.toughness,
            },
        }

    @classmethod
    def from_dict(cls, raw):
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "reference", "zone_change_counter", "last_known"}
                or evolve_entrant_lki_version(raw["schema_version"]) is None):
            raise EvolveError("Evolve entrant observation must use the closed versioned shape")
        last_known = raw["last_known"]
        if last_known is not None:
            if not isinstance(last_known, Mapping) or set(last_known) != {"is_creature", "power", "toughness"}:
                raise EvolveError("Evolve last-known characteristics must use the closed shape")
            last_known = EvolveCharacteristics(**last_known)
        return cls(raw["reference"], raw["zone_change_counter"], last_known)

    @classmethod
    def from_context(cls, context, *, use_observation):
        event = cls(context.get("card"), context.get("card_zone_change_counter"))
        if not use_observation or EVOLVE_ENTRANT_CONTEXT not in context:
            return event
        retained = cls.from_dict(context[EVOLVE_ENTRANT_CONTEXT])
        if (retained.reference, retained.zone_change_counter) != (event.reference, event.zone_change_counter):
            raise EvolveError("Evolve observation differs from its entering incarnation")
        return retained


def evolve_condition_holds(
    source: EvolveCharacteristics,
    entered: EvolveCharacteristics,
) -> bool:
    """Return the intervening-if result for one Evolve instance."""

    if not isinstance(source, EvolveCharacteristics) or not isinstance(
        entered, EvolveCharacteristics
    ):
        raise EvolveError("Evolve comparisons require typed characteristics")
    if not source.is_creature or not entered.is_creature:
        return False
    return (
        entered.power > source.power
        or entered.toughness > source.toughness
    )


__all__ = [
    "EVOLVE_EVENT_CONDITION_FIELD",
    "EVOLVE_ENTRANT_CONTEXT",
    "EVOLVE_ENTRANT_LKI_VERSION",
    "EVOLVE_ENTRANT_LKI_VERSION_FIELD",
    "EvolveEntrantObservation",
    "EvolveCharacteristics",
    "EvolveError",
    "evolve_condition_holds",
    "evolve_entrant_lki_fields",
    "evolve_entrant_lki_version",
]
