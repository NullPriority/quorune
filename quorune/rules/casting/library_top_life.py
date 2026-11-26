from __future__ import annotations

"""Price one selected library spell face under a live battlefield permission."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from ...errors import GameRuleError
from ...selection.exile_cast import mana_value_of_cost
from ...semantic_runtime.action_permissions import controller_life_priced_library_top_permissions
from ...util import parse_mana_symbols, stable_json
from .face_queries import current_printed_cast_mana_cost


LIBRARY_TOP_LIFE_PRICE_FIELD = "library_top_life_price"


@dataclass(frozen=True, slots=True)
class LibraryTopLifePrice:
    source_ref: str
    source_logical_object_id: str
    source_program: str
    life: int

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value
               for value in (self.source_ref, self.source_logical_object_id, self.source_program)):
            raise ValueError("A library-top life price requires its original permission source")
        if type(self.life) is not int or self.life < 0:
            raise ValueError("A library-top life price must be a nonnegative whole amount")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "source_ref": self.source_ref,
            "source_logical_object_id": self.source_logical_object_id,
            "source_program": self.source_program,
            "life": self.life,
        }

    @classmethod
    def from_dict(cls, value: object) -> LibraryTopLifePrice:
        if not isinstance(value, Mapping) or set(value) != {
            "source_ref", "source_logical_object_id", "source_program", "life",
        }:
            raise ValueError("The recorded library-top life price is malformed")
        return cls(**value)

    def cost_option(self, *, printed_x: bool) -> dict[str, Any]:
        authority = self.to_dict()
        identity = sha256(stable_json(authority).encode("utf-8")).hexdigest()[:24]
        return {
            "id": f"library-top-life:{identity}",
            "kind": "alternate",
            "label": f"Pay {self.life} life to cast from the library",
            "requirements": {},
            LIBRARY_TOP_LIFE_PRICE_FIELD: authority,
            **({"x_value_policy": "zero"} if printed_x else {}),
        }


def library_top_life_cost_options(host, seat, card, response, *, face_down: bool) -> tuple[dict[str, Any], ...]:
    if face_down or card.zone != "library":
        return ()
    sources = controller_life_priced_library_top_permissions(host, seat, card)
    if not sources:
        return ()
    record = host.card_record(card)
    if record is None:
        return ()
    cost = current_printed_cast_mana_cost(host, card, record)
    printed_x = "X" in parse_mana_symbols(cost)
    announced_x = response.get("x")
    if printed_x and announced_x is not None and (type(announced_x) is not int or announced_x != 0):
        return ()
    try:
        value = mana_value_of_cost(cost)
    except GameRuleError:
        return ()
    if not value.is_integer():
        return ()
    return tuple(
        LibraryTopLifePrice(permission.source_ref, permission.source_logical_object_id,
                            permission.source_semantic_key, int(value)).cost_option(printed_x=printed_x)
        for permission in sources
    )


def validate_library_top_life_price(host, seat, card, option, response, *, face_down: bool):
    price = LibraryTopLifePrice.from_dict(option.get(LIBRARY_TOP_LIFE_PRICE_FIELD))
    choices = library_top_life_cost_options(host, seat, card, response, face_down=face_down)
    if not any(candidate[LIBRARY_TOP_LIFE_PRICE_FIELD] == price.to_dict() for candidate in choices):
        raise ValueError("The quoted library-top life price is no longer available")
    return price


def library_top_life_amount(option: Mapping[str, Any]) -> int:
    if LIBRARY_TOP_LIFE_PRICE_FIELD not in option:
        return 0
    return LibraryTopLifePrice.from_dict(option[LIBRARY_TOP_LIFE_PRICE_FIELD]).life
