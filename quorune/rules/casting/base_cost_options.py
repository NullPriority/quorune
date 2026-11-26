from __future__ import annotations

"""Shared zone-authorized base alternatives before additional costs and modifiers."""

import copy
from collections.abc import Mapping, Sequence
from typing import Any, TYPE_CHECKING
from ...compiled_flashback import compiled_fixed_mana_flashback_spec, compiled_ordinary_zone_cast_permission
from .lifecycle_costs import retrace_base_options, zone_lifecycle_base_options
from ..self_graveyard_discard import self_graveyard_discard_base_options
from ..graveyard_cast_costs import paid_graveyard_base_options


if TYPE_CHECKING:
    from .costs import CastCostHost


def initial_cast_base_authority(host, seat, card, alternative_base, *, force_without_mana_cost,
                                suppress_source_costs, resolving_exile_authority):
    from ...opponent_library_exile import opponent_library_exile_cast_mode
    from .disturb_costs import disturb_back_cast_permission, with_more_than_meets_the_eye_base
    from .library_top_life import LIBRARY_TOP_LIFE_PRICE_FIELD

    temporary_permission = host._temporary_play_permission(seat, card)
    intrinsic_disturb = (disturb_back_cast_permission(host, seat, card)
                         and not (force_without_mana_cost or suppress_source_costs or alternative_base is not None))
    alternative_base = with_more_than_meets_the_eye_base(
        host, card, alternative_base,
        suppressed=force_without_mana_cost or suppress_source_costs or intrinsic_disturb,
    )
    library_life_base = alternative_base is not None and LIBRARY_TOP_LIFE_PRICE_FIELD in alternative_base
    cast_without_mana, separately_payable = opponent_library_exile_cast_mode(
        host.state, seat, card, temporary_permission,
        force_without_mana_cost=force_without_mana_cost, alternative_base=alternative_base,
        intrinsic_disturb=intrinsic_disturb, resolving_exile_authority=resolving_exile_authority,
        library_life_base=library_life_base,
    )
    return temporary_permission, intrinsic_disturb, alternative_base, cast_without_mana, separately_payable


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
    resolving_exile_authority: bool = False,
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
    from ...adventure import adventure_cast_permission
    if (
        card.zone == "exile"
        and not compiled_ordinary_zone_cast_permission(host, seat, card)
        and not preparation_cast_permission(host,seat,card)
        and not adventure_cast_permission(host,seat,card)
        and not force_without_mana_cost
        and alternative_base is None
        and not resolving_exile_authority
    ):
        base.clear()
    common = {
        "cast_without_mana": cast_without_mana,
        "force_without_mana_cost": force_without_mana_cost,
        "suppress_source_costs": suppress_source_costs,
    }
    base.extend(retrace_base_options(host, card, printed, **common))
    base.extend(self_graveyard_discard_base_options(host,seat,card,printed,**common))
    base.extend(paid_graveyard_base_options(host,seat,card,printed,**common))
    base.extend(
        zone_lifecycle_base_options(host, seat, card, printed, **common)
    )
    return base
