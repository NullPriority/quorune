from __future__ import annotations

"""Typed current-ability query for CR 514.1 maximum hand size."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .ability_fragments import StaticComponentSpec


NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID = "hand.cleanup.no_maximum"
NO_MAXIMUM_HAND_SIZE_HANDLER_ID = "rule.cleanup.no-maximum-hand-size.v1"
NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID = "no-maximum-hand-size-v1"


class MaximumHandSizeError(ValueError):
    """A maximum-hand-size descriptor or query is malformed."""


@dataclass(frozen=True, slots=True)
class NoMaximumHandSizeSpec:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise MaximumHandSizeError(
                "Unsupported no-maximum-hand-size schema version"
            )

    def to_dict(self) -> dict[str, int]:
        return {"schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NoMaximumHandSizeSpec":
        if not isinstance(value, Mapping) or set(value) != {"schema_version"}:
            raise MaximumHandSizeError(
                "No-maximum-hand-size specs use a closed schema"
            )
        return cls(schema_version=value["schema_version"])


def no_maximum_hand_size_handler_descriptor() -> dict[str, Any]:
    return {
        "handler_id": NO_MAXIMUM_HAND_SIZE_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "spec": NoMaximumHandSizeSpec().to_dict(),
    }


class MaximumHandSizeHost(Protocol):
    state: Any
    semantics: Any

    def _effective_ability_fragments(self, card: Any) -> tuple[Any, ...]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def controller_has_no_maximum_hand_size(
    host: MaximumHandSizeHost,
    seat: str,
) -> bool:
    """Query current controlled components without mutating player state."""

    for object_id in host.state.players[seat].zones["battlefield"]:
        source = host.state.cards[object_id]
        if source.controller != seat or source.phased_out:
            continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment, StaticComponentSpec):
                continue
            program = host.semantics.get(fragment.semantic_key)
            if (
                program is not None
                and host.semantic_program_is_current_trusted(program)
                and any(
                    descriptor.get("handler_id")
                    == NO_MAXIMUM_HAND_SIZE_HANDLER_ID
                    for descriptor in program.handlers
                )
            ):
                return True
    return False


def effective_maximum_hand_size(
    host: MaximumHandSizeHost,
    seat: str,
) -> int | None:
    if controller_has_no_maximum_hand_size(host, seat):
        return None
    value = host.state.players[seat].max_hand_size
    if type(value) is not int or value < 0:
        raise MaximumHandSizeError(
            "Maximum hand size must be a nonnegative integer"
        )
    return value


__all__ = [
    "NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID",
    "NO_MAXIMUM_HAND_SIZE_HANDLER_ID",
    "NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID",
    "MaximumHandSizeError",
    "MaximumHandSizeHost",
    "NoMaximumHandSizeSpec",
    "controller_has_no_maximum_hand_size",
    "effective_maximum_hand_size",
    "no_maximum_hand_size_handler_descriptor",
]
