from __future__ import annotations

"""Typed current-ability queries for finite and unbounded cleanup limits."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .ability_fragments import StaticComponentSpec


NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID = "hand.cleanup.no_maximum"
NO_MAXIMUM_HAND_SIZE_HANDLER_ID = "rule.cleanup.no-maximum-hand-size.v1"
NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID = "no-maximum-hand-size-v1"
HAND_SIZE_ADJUSTMENT_CAPABILITY_ID = "hand.cleanup.fixed_adjustment"
HAND_SIZE_ADJUSTMENT_HANDLER_ID = "rule.cleanup.fixed-hand-size-adjustment.v1"
HAND_SIZE_ADJUSTMENT_TEMPLATE_ID = "fixed-hand-size-adjustment-v1"


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


@dataclass(frozen=True, slots=True)
class FixedHandSizeAdjustmentSpec:
    player_scope: str
    amount: int
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.player_scope) is not str or self.player_scope not in {'controller','opponents'}
                or type(self.amount) is not int or self.amount == 0):
            raise MaximumHandSizeError('Hand-size adjustments require a fixed signed amount and owned player relation')

    def to_dict(self):
        return {'schema_version':self.schema_version,'player_scope':self.player_scope,'amount':self.amount}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','player_scope','amount'}:
            raise MaximumHandSizeError('Hand-size adjustment fields are incomplete or unknown')
        return cls(value['player_scope'],value['amount'],value['schema_version'])


def fixed_hand_size_adjustment_descriptor(spec):
    if not isinstance(spec,FixedHandSizeAdjustmentSpec):
        raise MaximumHandSizeError('Hand-size component requires its typed adjustment')
    return {'handler_id':HAND_SIZE_ADJUSTMENT_HANDLER_ID,'schema_version':1,
        'event':'characteristics.evaluate','spec':spec.to_dict()}


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
    return max(0,value + sum(current_hand_size_adjustments(host,seat)))


def current_hand_size_adjustments(host: MaximumHandSizeHost,seat: str) -> tuple[int,...]:
    result=[]
    for source in host.state.cards.values():
        if source.zone!='battlefield' or source.phased_out:continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment,StaticComponentSpec):continue
            program=host.semantics.get(fragment.semantic_key)
            if program is None or not host.semantic_program_is_current_trusted(program):continue
            for descriptor in program.handlers:
                if descriptor.get('handler_id')!=HAND_SIZE_ADJUSTMENT_HANDLER_ID:continue
                spec=FixedHandSizeAdjustmentSpec.from_dict(descriptor.get('spec'))
                if (seat==source.controller)==(spec.player_scope=='controller'):
                    result.append(spec.amount)
    return tuple(result)


__all__ = [
    "HAND_SIZE_ADJUSTMENT_CAPABILITY_ID",
    "HAND_SIZE_ADJUSTMENT_HANDLER_ID",
    "HAND_SIZE_ADJUSTMENT_TEMPLATE_ID",
    "FixedHandSizeAdjustmentSpec",
    "current_hand_size_adjustments",
    "fixed_hand_size_adjustment_descriptor",
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
