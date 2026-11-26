"""Announced-X affordability through normal cast price owners."""

from __future__ import annotations

from collections.abc import Collection
import copy
from typing import Any, Mapping, Sequence, TYPE_CHECKING
from ...printed_mana_cost_model import cast_snow_payment_kwargs
from .colored_hand_credit import HAND_CREDIT_AMOUNT

if TYPE_CHECKING:
    from .costs import CastCostHost


def quoted_printed_x_price(host, card, option, response):
    """Keep the exact base and surcharges behind a composed X quote."""
    from ...kicker import POSITIVE_GENERIC_KICKER_X
    from ...cast_lifecycles import GRANTED_ESCAPE_USE_FIELD
    from ...phasing_model import has_announced_phase_out_targets
    from ..announced_mana_value_target import ANNOUNCED_MANA_VALUE_LIMIT
    from .spell_text import CAST_TEXT_FRAMES

    spliced_phasing = CAST_TEXT_FRAMES in option and has_announced_phase_out_targets(option.get("effects", ()))
    kicker_x = option.get(POSITIVE_GENERIC_KICKER_X)
    granted_escape = GRANTED_ESCAPE_USE_FIELD in option
    if kicker_x is None and not option.get(ANNOUNCED_MANA_VALUE_LIMIT) and not (spliced_phasing or granted_escape):
        return None

    units = 1
    if spliced_phasing or granted_escape:
        from ...util import parse_mana_symbols
        from .face_queries import current_printed_cast_mana_cost

        record = host.card_record(card)
        if record is None:
            return None
        units = parse_mana_symbols(current_printed_cast_mana_cost(host, card, record)).count("X")
        if not units:
            return None
    requirements = dict(option["base_requirements"])
    charged_x = kicker_x if kicker_x is not None else response.get("x") or 0
    requirements["GENERIC"] -= units * charged_x
    price = {"requirements": requirements}
    if "snow" in option:
        price["snow"] = option["snow"]
    return price, units


def _maximum_affordable_x_with_mechanics(
    host: CastCostHost,
    seat: str,
    card: Any,
    program: Any,
    mechanics: Sequence[Mapping[str, Any]],
    *,
    spend_context: Any,
    minimum: int = 0,
    limit: int = 100,
    reserved_cards: Sequence[Any] = (),
    targeted_cost: Mapping[str, Any] | None = None,
    generic_credit: int = 0,
    reserved_credit_sources: Collection[str] = (),
    quoted_price: tuple[Mapping[str, Any], int] | None = None,
) -> int:
    from ...semantic_runtime.cast_costs import active_spell_mana_floor, apply_final_spell_mana_floor
    from .costs import _apply_payment_mechanics
    from .static_modifiers import apply_cast_static_reductions

    mana_floor = active_spell_mana_floor(host)
    credit_sources = set(reserved_credit_sources)
    maximum = -1
    for value in range(minimum, limit + generic_credit + 1):
        if quoted_price is None:
            printed, _ = host._compiled_printed_cost_options(
                seat, card, x_value=value, hint=False,
            )
        else:
            base, units = quoted_price
            requirements = dict(base["requirements"])
            requirements["GENERIC"] += units * value
            printed = ({**base, "requirements": requirements},)
        value_payable = False
        for raw_option in printed:
            option = copy.deepcopy(raw_option)
            option["requirements"] = host._mana_vector(option["requirements"])
            if targeted_cost is not None:
                option['targeted_cost'] = targeted_cost
                from .targeted_costs import apply_target_cost_increase
                apply_target_cost_increase(option)
            if generic_credit:
                option[HAND_CREDIT_AMOUNT] = generic_credit
            apply_cast_static_reductions(
                host,
                seat,
                card,
                option,
                program=program,
            )
            apply_final_spell_mana_floor(option, mana_floor)
            payment = _apply_payment_mechanics(
                host,
                seat,
                option,
                mechanics,
                {},
                hint=True,
                spend_context=spend_context,
                reserved_cards=reserved_cards,
            )
            if payment is None:
                continue
            _, selected = payment
            if host._cost_is_affordable(
                seat,
                option["requirements"],
                exclude_sources={source.object_id for source in selected} | credit_sources,
                spend_context=spend_context,
                **cast_snow_payment_kwargs(option),
            ):
                value_payable = True
                break
        if not value_payable:
            break
        maximum = value
    return maximum
