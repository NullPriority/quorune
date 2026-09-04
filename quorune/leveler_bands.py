from __future__ import annotations

"""Typed CR 711 level-symbol ranges and current-ability membership."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


LEVELER_BANDS_CAPABILITY_ID = "continuous.characteristics.leveler_bands"
LEVELER_BANDS_HANDLER_ID = "continuous.characteristics.leveler-bands.v1"
LEVELER_MECHANIC_ID = "cr-711-leveler-cards"


class LevelerBandError(ValueError):
    """A level-symbol range or membership descriptor is malformed."""


def _level_range(
    minimum_level: Any,
    maximum_level: Any,
) -> tuple[int, int | None]:
    if type(minimum_level) is not int or minimum_level < 1:
        raise LevelerBandError("Level-band minimums must be positive integers")
    if maximum_level is not None and (
        type(maximum_level) is not int or maximum_level < minimum_level
    ):
        raise LevelerBandError(
            "Level-band maximums must be null or integers at least the minimum"
        )
    return minimum_level, maximum_level


def _canonical_strings(
    values: Sequence[Any],
    *,
    field: str,
    casefold_unique: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise LevelerBandError(f"{field} must be an array")
    result = tuple(values)
    if any(
        type(value) is not str
        or not value.strip()
        or value != value.strip()
        for value in result
    ):
        raise LevelerBandError(f"{field} must contain canonical strings")
    normalized = tuple(
        value.casefold() if casefold_unique else value for value in result
    )
    if len(set(normalized)) != len(result):
        raise LevelerBandError(f"{field} must contain unique values")
    if result != tuple(sorted(result, key=str.casefold)):
        raise LevelerBandError(f"{field} must use canonical sorted order")
    return result


@dataclass(frozen=True, slots=True)
class LevelerBandSpec:
    """One level-symbol range with fixed base P/T and typed abilities."""

    minimum_level: int
    maximum_level: int | None
    power: int
    toughness: int
    keywords: tuple[str, ...] = ()
    semantic_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _level_range(self.minimum_level, self.maximum_level)
        if (
            type(self.power) is not int
            or self.power < 0
            or type(self.toughness) is not int
            or self.toughness < 0
        ):
            raise LevelerBandError(
                "Level-band power and toughness must be nonnegative integers"
            )
        keywords = _canonical_strings(
            self.keywords,
            field="Level-band keywords",
            casefold_unique=True,
        )
        semantic_keys = _canonical_strings(
            self.semantic_keys,
            field="Level-band semantic keys",
        )
        object.__setattr__(self, "keywords", keywords)
        object.__setattr__(self, "semantic_keys", semantic_keys)

    def permits(self, level_count: int) -> bool:
        if type(level_count) is not int or level_count < 0:
            raise LevelerBandError(
                "Level counters must be a nonnegative integer"
            )
        return bool(
            level_count >= self.minimum_level
            and (
                self.maximum_level is None
                or level_count <= self.maximum_level
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_level": self.minimum_level,
            "maximum_level": self.maximum_level,
            "power": self.power,
            "toughness": self.toughness,
            "keywords": list(self.keywords),
            "semantic_keys": list(self.semantic_keys),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LevelerBandSpec":
        expected = {
            "minimum_level",
            "maximum_level",
            "power",
            "toughness",
            "keywords",
            "semantic_keys",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise LevelerBandError("Leveler bands have a closed schema")
        if not isinstance(value["keywords"], list) or not isinstance(
            value["semantic_keys"], list
        ):
            raise LevelerBandError(
                "Leveler band keywords and semantic keys must be arrays"
            )
        return cls(
            minimum_level=value["minimum_level"],
            maximum_level=value["maximum_level"],
            power=value["power"],
            toughness=value["toughness"],
            keywords=tuple(value["keywords"]),
            semantic_keys=tuple(value["semantic_keys"]),
        )


@dataclass(frozen=True, slots=True)
class LevelerBandsSpec:
    """The two nonoverlapping level-symbol abilities on one Leveler."""

    bands: tuple[LevelerBandSpec, LevelerBandSpec]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise LevelerBandError("Unsupported Leveler-band schema version")
        if (
            not isinstance(self.bands, tuple)
            or len(self.bands) != 2
            or any(not isinstance(band, LevelerBandSpec) for band in self.bands)
        ):
            raise LevelerBandError("Leveler cards require exactly two typed bands")
        lower, upper = self.bands
        if (
            lower.maximum_level is None
            or upper.maximum_level is not None
            or upper.minimum_level != lower.maximum_level + 1
        ):
            raise LevelerBandError(
                "Leveler bands require one finite range followed by its open range"
            )
        semantic_keys = tuple(
            key for band in self.bands for key in band.semantic_keys
        )
        if len(semantic_keys) != len(set(semantic_keys)):
            raise LevelerBandError(
                "One semantic component cannot belong to two level bands"
            )

    @property
    def keywords(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    keyword
                    for band in self.bands
                    for keyword in band.keywords
                },
                key=str.casefold,
            )
        )

    def active_band(
        self,
        counters: Mapping[str, Any],
    ) -> LevelerBandSpec | None:
        if not isinstance(counters, Mapping):
            raise LevelerBandError("Leveler counters must be a mapping")
        level_count = counters.get("level", 0)
        if type(level_count) is not int or level_count < 0:
            raise LevelerBandError(
                "Level counters must be a nonnegative integer"
            )
        matching = tuple(
            band for band in self.bands if band.permits(level_count)
        )
        if len(matching) > 1:
            raise LevelerBandError("Leveler ranges cannot overlap")
        return matching[0] if matching else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bands": [band.to_dict() for band in self.bands],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LevelerBandsSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "bands",
        }:
            raise LevelerBandError("Leveler-band sets have a closed schema")
        raw_bands = value["bands"]
        if not isinstance(raw_bands, list) or len(raw_bands) != 2:
            raise LevelerBandError("Leveler-band sets require two bands")
        return cls(
            bands=tuple(LevelerBandSpec.from_dict(band) for band in raw_bands),
            schema_version=value["schema_version"],
        )


def leveler_bands_handler_descriptor(
    spec: LevelerBandsSpec,
) -> dict[str, Any]:
    return {
        "handler_id": LEVELER_BANDS_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "bands": [band.to_dict() for band in spec.bands],
    }


__all__ = [
    "LEVELER_BANDS_CAPABILITY_ID",
    "LEVELER_BANDS_HANDLER_ID",
    "LEVELER_MECHANIC_ID",
    "LevelerBandError",
    "LevelerBandSpec",
    "LevelerBandsSpec",
    "leveler_bands_handler_descriptor",
]
