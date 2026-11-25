from __future__ import annotations

"""Numeric exchange of a life total and one characteristic of a source."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import thaw_value


CHARACTERISTIC_LIFE_EXCHANGE_OPERATION = "exchange_life_and_source_characteristic"
CHARACTERISTIC_LIFE_EXCHANGE_CAPABILITY = "life.change.characteristic_exchange"
CHARACTERISTIC_LIFE_EXCHANGE_RULES = ("119.7", "119.8", "613.4b", "701.12a", "701.12g")


@dataclass(frozen=True, slots=True)
class CharacteristicLifeExchangeSpec:
    characteristic: str
    recipient: str = "controller"
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or type(self.characteristic) is not str or self.characteristic not in {"power", "toughness"}
            or type(self.recipient) is not str or self.recipient not in {"controller", "target_opponent"}
            or self.recipient == "target_opponent" and self.characteristic != "toughness"
        ):
            raise ValueError("Life and characteristic exchange requires a closed recipient and characteristic")

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "characteristic", "recipient"}:
            raise ValueError("Life and characteristic exchange requires its complete descriptor")
        return cls(**dict(value))

    def to_dict(self):
        return {"schema_version": self.schema_version, "characteristic": self.characteristic,
                "recipient": self.recipient}

    def to_instruction(self):
        player = "$controller" if self.recipient == "controller" else "$target.0"
        return {"op": CHARACTERISTIC_LIFE_EXCHANGE_OPERATION, "exchange": self.to_dict(),
                "source": "$source.zone_object", "player": player}

    def to_target_schema(self):
        return ({"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}
                if self.recipient == "target_opponent" else None)


def exchange_instruction_dependencies(effects, target_schema):
    if len(effects) != 1:
        raise ValueError("An exchange must be one complete instruction")
    spec = CharacteristicLifeExchangeSpec.from_mapping(effects[0].get("exchange"))
    if thaw_value(effects[0]) != spec.to_instruction() or thaw_value(target_schema) != spec.to_target_schema():
        raise ValueError("An exchange must retain its source, characteristic, and recipient role")
    return (
        CHARACTERISTIC_LIFE_EXCHANGE_CAPABILITY, "life.change.effect",
        *(("target.revalidate_resolution",) if target_schema is not None else ()),
    )


def _contains_exchange(value):
    if isinstance(value, Mapping):
        return value.get("op") == CHARACTERISTIC_LIFE_EXCHANGE_OPERATION or any(
            _contains_exchange(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_contains_exchange(child) for child in value)


def characteristic_life_exchange_program_dependencies(program):
    if not (_contains_exchange(program.effects) or _contains_exchange(program.target_schema)):
        return ()
    if program.event != "activate" or program.active_zone != "battlefield":
        raise ValueError("Characteristic exchange requires a battlefield activation")
    return exchange_instruction_dependencies(program.effects, program.target_schema)


@dataclass(frozen=True, slots=True)
class CharacteristicLifeExchange:
    life_delta: int
    new_base_characteristic: int


def plan_characteristic_life_exchange(
    *, life_total: int, effective_characteristic: int, can_gain_life: bool, can_lose_life: bool,
) -> CharacteristicLifeExchange | None:
    """Derive both requests before either request can modify current values."""
    if type(life_total) is not int or type(effective_characteristic) is not int:
        raise ValueError("Exchange requires two exact numeric game values")
    if type(can_gain_life) is not bool or type(can_lose_life) is not bool:
        raise ValueError("Exchange requires both current life-change permissions")
    delta = effective_characteristic - life_total
    if (delta > 0 and not can_gain_life) or (delta < 0 and not can_lose_life):
        return None
    return CharacteristicLifeExchange(delta, life_total)
