from __future__ import annotations

"""Retained public continuity facts for an original duration source."""

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping


def _nonnegative_fields(value: Any) -> None:
    if any(type(getattr(value, field.name)) is not int or getattr(value, field.name) < 0
           for field in fields(value)):
        raise ValueError("Source continuity facts must be nonnegative exact integers")


@dataclass(frozen=True, slots=True)
class SourceContinuitySnapshot:
    control_epoch: int
    untap_epoch: int
    phase_epoch: int

    def __post_init__(self) -> None:
        _nonnegative_fields(self)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SourceContinuitySnapshot:
        if not isinstance(value, Mapping) or set(value) != {field.name for field in fields(cls)}:
            raise ValueError("Source continuity snapshot has missing or unknown facts")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class SourceContinuityHistory:
    control_epoch: int = 0
    untap_epoch: int = 0
    phase_epoch: int = 0
    last_control_timestamp: int = 0
    last_tap_timestamp: int = 0
    last_phase_in_timestamp: int = 0

    def __post_init__(self) -> None:
        _nonnegative_fields(self)

    def snapshot(self) -> SourceContinuitySnapshot:
        return SourceContinuitySnapshot(self.control_epoch, self.untap_epoch, self.phase_epoch)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SourceContinuityHistory:
        if not isinstance(value, Mapping) or set(value) != {field.name for field in fields(cls)}:
            raise ValueError("Source continuity history has missing or unknown facts")
        return cls(**value)
