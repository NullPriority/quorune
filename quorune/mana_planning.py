from __future__ import annotations

"""Ordered source selection when a compiled mana mode has a mana price."""

from dataclasses import dataclass
from typing import Callable

from .mana import MANA_COLORS, ManaMode, ManaSource
from .mana_provenance import (
    PlanningManaPool, add_mana, mana_provenance_lots, snow_mana_pool, spend_mana, spendable_mana_pool,
)
from .util import normalize_mana_bundle, pay_mana_from_pool


@dataclass(frozen=True, slots=True)
class ManaPlanFunding:
    pool: PlanningManaPool
    restriction_allows: Callable[[str, str | None], bool]
    spend_context: str | None


def _output_capacity(modes):
    modes = tuple(modes)
    return {color: max((mode.bundle.get(color, 0) for mode in modes), default=0) for color in MANA_COLORS}


def _optimistic_pool(pool, capacities):
    result = normalize_mana_bundle(pool)
    for capacity in capacities:
        for color, amount in capacity.items():
            result[color] += amount
    return result


def _can_fund(price, pool):
    try:
        pay_mana_from_pool(pool, price.option.requirements, spending_policy=price.spending_policy)
    except ValueError:
        return False
    return True


def _exact_bundles(pool, amount, colors=MANA_COLORS):
    if not amount:
        yield {}
        return
    if not colors:
        return
    color, *rest = colors
    for count in range(min(pool[color], amount), -1, -1):
        for suffix in _exact_bundles(pool, amount - count, rest):
            yield {**suffix, **({color: count} if count else {})}


def available_mode_payments(mode, pool, allows):
    if mode.price is None:
        yield {}
        return
    price = mode.price
    available = spendable_mana_pool(pool, restriction_allows=allows, spend_context=price.spend_context)
    if not _can_fund(price, available):
        return
    for bundle in _exact_bundles(available, sum(price.option.requirements.values())):
        try:
            pay_mana_from_pool(available, price.option.requirements, payment=bundle, spending_policy=price.spending_policy)
        except ValueError:
            continue
        yield bundle


def _pool_after_step(pool, source, mode, payment, allows):
    next_pool = PlanningManaPool.capture(pool)
    if mode.price is not None:
        spend_mana(next_pool, payment, snow_payment=None, restriction_allows=allows, spend_context=mode.price.spend_context)
    add_mana(next_pool, mode.bundle, snow=source.snow, restriction=mode.restriction or None)
    return next_pool


def _ordered_mode_allowed(mode, contexts, allows, allow_conditional):
    if mode.requires_choice:
        return False
    if mode.intermediate_only:
        return any(allows(mode.restriction, context) for context in contexts)
    return allow_conditional or not mode.conditional


def ordered_mana_selection(
    sources: tuple[ManaSource, ...],
    funding: ManaPlanFunding,
    can_pay: Callable[[dict[str, int], dict[str, int]], bool],
    *,
    allow_conditional: bool,
) -> tuple[list[tuple[ManaSource, ManaMode, dict[str, int]]], dict[str, int], dict[str, int]] | None:
    """Find a legal funding order, with one committed mode per source."""
    contexts = {mode.price.spend_context for source in sources for mode in source.modes if mode.price}
    if not contexts:
        return None
    allows = funding.restriction_allows
    groups = tuple((source, tuple(mode for mode in source.modes
        if _ordered_mode_allowed(mode, contexts, allows, allow_conditional))) for source in sources)
    groups = tuple((source, modes) for source, modes in groups if modes)
    seeds = {
        context: _optimistic_pool(
            spendable_mana_pool(funding.pool, restriction_allows=allows, spend_context=context),
            (_output_capacity(mode for mode in modes if mode.price is None and
                              (not mode.restriction or allows(mode.restriction, context))) for _source, modes in groups),
        )
        for context in contexts
    }
    if not any(mode.price and _can_fund(mode.price, seeds[mode.price.spend_context])
               for _source, modes in groups for mode in modes):
        return None
    capacities = tuple(_output_capacity(modes) for _source, modes in groups)
    failed = set()

    def visit(pool, remaining, steps):
        spendable = spendable_mana_pool(pool, restriction_allows=allows, spend_context=funding.spend_context)
        snow = snow_mana_pool(pool, restriction_allows=allows, spend_context=funding.spend_context)
        if can_pay(spendable, snow):
            return list(steps), spendable, snow
        key = remaining, tuple(pool.mana_pool[color] for color in MANA_COLORS), mana_provenance_lots(pool)
        if key in failed:
            return None
        optimistic = _optimistic_pool(pool.mana_pool, (capacities[index] for index in remaining))
        if can_pay(optimistic, optimistic):
            for index in sorted(remaining):
                source, modes = groups[index]
                for mode in modes:
                    for payment in available_mode_payments(mode, pool, allows):
                        next_pool = _pool_after_step(pool, source, mode, payment, allows)
                        selected = visit(next_pool, remaining - {index}, (*steps, (source, mode, payment)))
                        if selected is not None:
                            return selected
        failed.add(key)
        return None

    return visit(funding.pool, frozenset(range(len(groups))), ())
