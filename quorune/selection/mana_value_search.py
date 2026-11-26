"""Mana value legality for private library search results."""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Mapping

from ..errors import GameRuleError
from ..rules.library_search_rules import library_search_object_ids


_FRONT_FACE_LAYOUTS = frozenset({"adventure", "flip", "modal_dfc", "prepare", "transform"})


def _library_creature_mana_value(host, card) -> float | None:
    """Read current library characteristics, including applicable all-zone effects."""
    record = host.card_record(card)
    if record is None or card.is_token:
        return None
    if record.faces and record.layout in _FRONT_FACE_LAYOUTS:
        card = replace(card, active_face=record.faces[0]["name"])
    effective = host._effective_card_data(card)
    types, _, _ = host._type_parts(str(effective.get("type_line") or ""))
    if "creature" not in types:
        return None
    value = float(effective["mana_value"])
    return value if isfinite(value) and value >= 0 else None


def creature_subset_search_options(host, seat, maximum: int):
    options = []
    for object_id in library_search_object_ids(host, seat, "library"):
        card = host.state.cards[object_id]
        mana_value = _library_creature_mana_value(host, card)
        if mana_value is not None and mana_value <= maximum:
            options.append(
                {
                    "id": card.ref,
                    "name": card.printed_name,
                    "mana_value": mana_value,
                    "label": f"{card.printed_name} (mana value {mana_value:g})",
                }
            )
    return options


def _validate_search_mana_value_total(total: float, constraint: Mapping) -> None:
    minimum = constraint.get("minimum")
    maximum = constraint.get("maximum")
    if (minimum is not None and total < float(minimum)) or (
        maximum is not None and total > float(maximum)
    ):
        raise GameRuleError(
            "Selected search cards do not satisfy the aggregate mana-value constraint"
        )


def validate_creature_subset_search_results(host, selected, maximum: int) -> None:
    total = 0.0
    for card in selected:
        value = _library_creature_mana_value(host, card)
        if value is None:
            raise GameRuleError("Selected search result is no longer a legal creature card")
        total += value
    _validate_search_mana_value_total(total, {"maximum": maximum})


def validate_legacy_search_mana_value_total(host, seat, selected, search_zones, constraint) -> None:
    zones = set(search_zones)
    total = sum(
        float(host.card_record(host._resolve_object(seat, ref, zones=zones, owned_only=True)).mana_value)
        for ref in selected
    )
    bounds = constraint if isinstance(constraint, Mapping) else {"maximum": constraint}
    _validate_search_mana_value_total(total, bounds)
