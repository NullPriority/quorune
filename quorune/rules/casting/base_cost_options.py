from __future__ import annotations

"""Shared zone-authorized base alternatives before additional costs and modifiers."""

import copy
from collections.abc import Mapping, Sequence
from typing import Any, TYPE_CHECKING
from ...compiled_flashback import compiled_fixed_mana_flashback_spec, compiled_ordinary_zone_cast_permission
from .lifecycle_costs import retrace_base_options, zone_lifecycle_base_options


if TYPE_CHECKING:
    from .costs import CastCostHost


def _flashback_base_options(
    host: CastCostHost,
    seat: str,
    card: Any,
    printed: Sequence[Mapping[str, Any]],
    *,
    cast_without_mana: bool,
    force_without_mana_cost: bool,
    suppress_source_costs: bool,
) -> list[dict[str, Any]]:
    """Add only the currently authorized Flashback casting-cost branch."""

    result = [copy.deepcopy(dict(value)) for value in printed]
    flashback = (
        compiled_fixed_mana_flashback_spec(host, card)
        if not suppress_source_costs and card.zone == "graveyard"
        else None
    )
    if (
        card.zone == "graveyard"
        and not cast_without_mana
        and not compiled_ordinary_zone_cast_permission(host, seat, card)
    ):
        result.clear()
    if flashback is not None and not force_without_mana_cost:
        result.append(flashback.cast_cost_option())
    return result


def _zone_cast_base_options(
    host: CastCostHost,
    seat: str,
    card: Any,
    printed: Sequence[Mapping[str, Any]],
    *,
    cast_without_mana: bool,
    force_without_mana_cost: bool,
    alternative_base: Mapping[str, Any] | None,
    suppress_source_costs: bool,
) -> list[dict[str, Any]]:
    base = _flashback_base_options(
        host,
        seat,
        card,
        printed,
        cast_without_mana=cast_without_mana,
        force_without_mana_cost=force_without_mana_cost,
        suppress_source_costs=suppress_source_costs,
    )
    from ...preparation import preparation_cast_permission
    if (
        card.zone == "exile"
        and not compiled_ordinary_zone_cast_permission(host, seat, card)
        and not preparation_cast_permission(host,seat,card)
        and not force_without_mana_cost
        and alternative_base is None
    ):
        base.clear()
    common = {
        "cast_without_mana": cast_without_mana,
        "force_without_mana_cost": force_without_mana_cost,
        "suppress_source_costs": suppress_source_costs,
    }
    base.extend(retrace_base_options(host, card, printed, **common))
    base.extend(
        zone_lifecycle_base_options(host, seat, card, printed, **common)
    )
    return base
