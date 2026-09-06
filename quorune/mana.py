from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from .carddb import CardRecord
from .intrinsic_basic_land_mana import BASIC_LAND_MANA
from .util import (
    mana_cost_to_vector,
    normalize_mana_bundle,
)

MANA_COLORS = ("W", "U", "B", "R", "G", "C")
SYMBOL_RE = re.compile(r"\{([WUBRGC])\}")
ADD_CLAUSE_RE = re.compile(
    r"\{T\}(?P<costs>(?:\s*,\s*[^:\n]+)?)\s*:\s*Add\s+(?P<output>[^\.\n]+)",
    re.IGNORECASE,
)
_DYNAMIC_OUTPUT_MARKERS = (
    "mana of any color among",
    "for each color among",
    "mana of each color among",
    "mana of any color that ",
)


@dataclass(frozen=True, slots=True)
class ManaMode:
    bundle: dict[str, int]
    conditional: bool = False
    restriction: str = ""
    side_effects: tuple[dict[str, Any], ...] = ()
    requires_choice: bool = False

    @property
    def total(self) -> int:
        return sum(self.bundle.values())

    def to_compact(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"m": {k: v for k, v in self.bundle.items() if v}}
        if self.conditional:
            payload["cond"] = self.restriction or True
        if self.side_effects:
            payload["fx"] = list(self.side_effects)
        if self.requires_choice:
            payload["choice"] = True
        return payload


@dataclass(frozen=True, slots=True)
class ManaSource:
    object_id: str
    ref: str
    name: str
    modes: tuple[ManaMode, ...]
    snow: bool = False

    def to_compact(self) -> dict[str, Any]:
        result = {
            "id": self.ref,
            "n": self.name,
            "modes": [mode.to_compact() for mode in self.modes],
        }
        if self.snow:
            result["snow"] = True
        return result


@dataclass(slots=True)
class ManaPlan:
    activations: list[dict[str, Any]] = field(default_factory=list)
    payment: dict[str, int] = field(default_factory=lambda: normalize_mana_bundle(None))
    snow_payment: dict[str, int] = field(
        default_factory=lambda: normalize_mana_bundle(None)
    )
    warnings: list[str] = field(default_factory=list)


class ManaPlanError(RuntimeError):
    pass


def effective_mana_record(
    record: CardRecord | None,
    effective: Mapping[str, Any],
) -> CardRecord | None:
    """Overlay current face/layer characteristics onto a printed record."""

    if record is None:
        return None
    return replace(
        record,
        name=str(effective.get("name") or record.name),
        type_line=str(effective.get("type_line") or ""),
        oracle_text=str(effective.get("oracle_text") or ""),
        keywords=tuple(effective.get("keywords") or ()),
        produced_mana=tuple(effective.get("produced_mana") or ()),
    )


def extract_effective_mana_modes(
    record: CardRecord,
    effective: Mapping[str, Any],
    commander_identity: Iterable[str] = (),
) -> tuple[ManaMode, ...]:
    return extract_mana_modes(
        effective_mana_record(record, effective) or record,
        commander_identity,
    )


def _bundle_from_symbols(text: str) -> dict[str, int]:
    bundle = normalize_mana_bundle(None)
    for symbol in SYMBOL_RE.findall(text.upper()):
        bundle[symbol] += 1
    return bundle


def _any_color_modes(quantity: int = 1, allowed: Iterable[str] = ("W", "U", "B", "R", "G")) -> list[ManaMode]:
    return [ManaMode({**normalize_mana_bundle(None), color: quantity}) for color in allowed]


def _has_unresolved_dynamic_output(oracle_text: str) -> bool:
    normalized = oracle_text.casefold()
    return any(marker in normalized for marker in _DYNAMIC_OUTPUT_MARKERS)


def _is_unrestricted_any_output(output_text: str) -> bool:
    return (
        "among" not in output_text
        and "that " not in output_text
        and (
            "one mana of any color" in output_text
            or "one mana of any type" in output_text
        )
    )


def extract_mana_modes(record: CardRecord, commander_identity: Iterable[str] = ()) -> tuple[ManaMode, ...]:
    """Conservatively extract tap-mana modes for packet display and auto-pay.

    Complex modes remain visible but are marked conditional/requires-choice, so
    the planner will not silently use them without an explicit override.
    """

    modes: list[ManaMode] = []
    type_line = record.type_line.casefold()
    oracle = record.oracle_text or ""
    oracle_lower = oracle.casefold()
    commander_colors = tuple(color for color in commander_identity if color in "WUBRG")

    # Basic land types confer intrinsic mana abilities.
    for basic_type, color in BASIC_LAND_MANA.items():
        if basic_type in type_line:
            bundle = normalize_mana_bundle(None)
            bundle[color] = 1
            modes.append(ManaMode(bundle))

    for match in ADD_CLAUSE_RE.finditer(oracle):
        clause = match.group("output").strip()
        extra_costs = match.group("costs").casefold()
        lower = clause.casefold()
        line_end = oracle.find("\n", match.start())
        ability_line = oracle[
            match.start() : (line_end if line_end >= 0 else len(oracle))
        ].strip()
        ability_lower = ability_line.casefold()
        conditional = any(
            marker in ability_lower
            for marker in (
                "only",
                "can't be spent",
                "if ",
                "unless",
                "for each",
                "equal to",
                "could produce",
                "among",
            )
        )
        requires_choice = any(
            marker in lower
            for marker in ("sacrifice", "remove a counter", "choose a color of a permanent")
        )
        side_effects_list: list[dict[str, Any]] = []
        self_damage = re.search(
            r"deals? (?P<amount>\d+) damage to you",
            ability_lower,
        )
        if self_damage:
            side_effects_list.append(
                {
                    "op": "damage_self",
                    "amount": int(self_damage.group("amount")),
                }
            )
        else:
            tapped_damage = re.search(
                r"whenever this land becomes tapped, it deals? "
                r"(?P<amount>\d+) damage to you",
                oracle_lower,
            )
            if tapped_damage:
                side_effects_list.append(
                    {
                        "op": "damage_self",
                        "amount": int(tapped_damage.group("amount")),
                    }
                )
        pay_life = re.search(r"pay\s+(\d+)\s+life", extra_costs)
        if pay_life:
            side_effects_list.append({"op": "pay_life", "amount": int(pay_life.group(1))})
        if re.search(r"\bsacrifice this (?:artifact|creature|land|permanent|token)\b", extra_costs):
            side_effects_list.append({"op": "sacrifice_source"})
        side_effects = tuple(side_effects_list)

        if "three mana of any one color" in lower:
            modes.extend(
                ManaMode(
                        mode.bundle,
                        conditional=conditional,
                        restriction=ability_line,
                        side_effects=side_effects,
                        requires_choice=requires_choice,
                    )
                for mode in _any_color_modes(3)
            )
            continue
        if "two mana of any one color" in lower:
            modes.extend(
                ManaMode(
                        mode.bundle,
                        conditional=conditional,
                        restriction=ability_line,
                        side_effects=side_effects,
                        requires_choice=requires_choice,
                    )
                for mode in _any_color_modes(2)
            )
            continue
        if "one mana of any color in your commander's color identity" in lower:
            allowed = commander_colors or tuple(color for color in record.color_identity if color in "WUBRG")
            modes.extend(
                ManaMode(
                        mode.bundle,
                        conditional=conditional,
                        restriction=ability_line,
                        side_effects=side_effects,
                        requires_choice=requires_choice,
                    )
                for mode in _any_color_modes(1, allowed)
            )
            continue
        if _is_unrestricted_any_output(lower):
            allowed = ("W", "U", "B", "R", "G", "C") if "any type" in lower else ("W", "U", "B", "R", "G")
            modes.extend(
                ManaMode(
                        mode.bundle,
                        conditional=conditional,
                        restriction=ability_line,
                        side_effects=side_effects,
                        requires_choice=requires_choice,
                    )
                for mode in _any_color_modes(1, allowed)
            )
            continue

        symbols = _bundle_from_symbols(clause)
        if sum(symbols.values()) > 0:
            # "{U} or {R}" is separate one-mana modes rather than a bundle.
            if " or " in lower and sum(symbols.values()) > 1:
                for color, amount in symbols.items():
                    if not amount:
                        continue
                    bundle = normalize_mana_bundle(None)
                    bundle[color] = 1
                    modes.append(
                        ManaMode(
                            bundle,
                            conditional=conditional,
                            restriction=ability_line,
                            side_effects=side_effects,
                            requires_choice=requires_choice,
                        )
                    )
            else:
                modes.append(
                    ManaMode(
                        symbols,
                        conditional=conditional,
                        restriction=ability_line,
                        side_effects=side_effects,
                        requires_choice=requires_choice,
                    )
                )

    # Scryfall's produced_mana is a useful fallback, but not enough evidence to
    # claim a complex source is unconditional.
    if not modes and record.produced_mana and not _has_unresolved_dynamic_output(oracle):
        # produced_mana says what a card can make, not whether the activation is
        # currently legal or what nonmana costs/restrictions apply. A fallback
        # is therefore always conditional and is never silently auto-spent by
        # the authoritative engine.
        fallback_conditional = True
        for color in record.produced_mana:
            if color not in MANA_COLORS:
                continue
            bundle = normalize_mana_bundle(None)
            bundle[color] = 1
            modes.append(
                ManaMode(
                    bundle,
                    conditional=fallback_conditional,
                    restriction="Oracle-dependent mana mode" if fallback_conditional else "",
                    requires_choice=fallback_conditional,
                )
            )

    # Deduplicate modes while retaining the stricter representation.
    dedup: dict[tuple[tuple[str, int], ...], ManaMode] = {}
    for mode in modes:
        key = tuple(sorted((k, v) for k, v in mode.bundle.items() if v))
        prior = dedup.get(key)
        if prior is None or (prior.conditional and not mode.conditional):
            dedup[key] = mode
    return tuple(dedup.values())


def parsed_cost(cost: str, commander_tax: int = 0) -> dict[str, int]:
    requirements, complex_symbols = mana_cost_to_vector(cost)
    if complex_symbols:
        raise ManaPlanError(f"Cost contains unsupported symbols: {complex_symbols}")
    requirements["GENERIC"] += commander_tax
    return requirements


def _ordinary_payment_sufficient(
    pool: Mapping[str, int],
    fixed_need: Mapping[str, int],
    generic_need: int,
    reserve: Mapping[str, int],
) -> bool:
    remaining = dict(pool)
    for color in MANA_COLORS:
        needed = fixed_need[color] + reserve[color]
        if remaining[color] < needed:
            return False
        remaining[color] -= fixed_need[color]
    return sum(remaining.values()) >= generic_need + sum(reserve.values())


def _snow_payment_option(
    pool: Mapping[str, int],
    snow_pool: Mapping[str, int],
    *,
    fixed_need: Mapping[str, int],
    generic_need: int,
    reserve: Mapping[str, int],
    snow_required: int,
) -> dict[str, int] | None:
    if not snow_required:
        return (
            normalize_mana_bundle(None)
            if _ordinary_payment_sufficient(
                pool,
                fixed_need,
                generic_need,
                reserve,
            )
            else None
        )
    option = normalize_mana_bundle(None)
    order = tuple(
        sorted(
            MANA_COLORS,
            key=lambda color: (
                pool[color] - fixed_need[color] - reserve[color],
                color == "C",
                color,
            ),
            reverse=True,
        )
    )

    def choose(index: int, remaining: int) -> dict[str, int] | None:
        if remaining == 0:
            after = dict(pool)
            for color in MANA_COLORS:
                after[color] -= option[color]
            return (
                dict(option)
                if _ordinary_payment_sufficient(
                    after,
                    fixed_need,
                    generic_need,
                    reserve,
                )
                else None
            )
        if index >= len(order):
            return None
        color = order[index]
        maximum = min(remaining, snow_pool[color], pool[color])
        for amount in range(maximum, -1, -1):
            option[color] = amount
            found = choose(index + 1, remaining - amount)
            if found is not None:
                return found
        option[color] = 0
        return None

    return choose(0, snow_required)


def _select_payment_sources(
    grouped: Sequence[tuple[ManaSource, Sequence[ManaMode]]],
    *,
    starting_pool: Mapping[str, int],
    starting_snow_pool: Mapping[str, int],
    fixed_need: Mapping[str, int],
    generic_need: int,
    reserve: Mapping[str, int],
    snow_required: int,
) -> list[tuple[ManaSource, ManaMode]] | None:
    target_total = (
        sum(fixed_need.values())
        + generic_need
        + snow_required
        + sum(reserve.values())
    )
    suffix_capacity = [0] * (len(grouped) + 1)
    for index in range(len(grouped) - 1, -1, -1):
        suffix_capacity[index] = suffix_capacity[index + 1] + max(
            mode.total for mode in grouped[index][1]
        )
    best: list[tuple[ManaSource, ManaMode]] | None = None

    def dfs(
        index: int,
        pool: dict[str, int],
        snow_pool: dict[str, int],
        chosen: list[tuple[ManaSource, ManaMode]],
    ) -> None:
        nonlocal best
        if best is not None and len(chosen) >= len(best):
            return
        if _snow_payment_option(
            pool,
            snow_pool,
            fixed_need=fixed_need,
            generic_need=generic_need,
            reserve=reserve,
            snow_required=snow_required,
        ) is not None:
            best = list(chosen)
            return
        if index >= len(grouped):
            return
        if sum(pool.values()) + suffix_capacity[index] < target_total:
            return
        source, modes = grouped[index]
        scored = sorted(
            modes,
            key=lambda mode: (
                -sum(
                    min(mode.bundle[color], fixed_need[color])
                    for color in MANA_COLORS
                ),
                -mode.total,
                mode.conditional,
            ),
        )
        for mode in scored:
            next_pool = dict(pool)
            next_snow_pool = dict(snow_pool)
            for color, amount in mode.bundle.items():
                next_pool[color] = next_pool.get(color, 0) + amount
                if source.snow:
                    next_snow_pool[color] = (
                        next_snow_pool.get(color, 0) + amount
                    )
            chosen.append((source, mode))
            dfs(index + 1, next_pool, next_snow_pool, chosen)
            chosen.pop()
        dfs(index + 1, pool, snow_pool, chosen)

    dfs(0, dict(starting_pool), dict(starting_snow_pool), [])
    return best


def auto_plan_payment(
    requirements: dict[str, int],
    sources: Iterable[ManaSource],
    *,
    allow_conditional: bool = False,
    reserve: dict[str, int] | None = None,
    starting_pool: dict[str, int] | None = None,
    snow_required: int = 0,
    starting_snow_pool: dict[str, int] | None = None,
) -> ManaPlan:
    """Find a deterministic one-activation-per-source payment plan.

    This planner is intentionally conservative.  Sources with sacrifice costs,
    opponent-dependent output, or other unresolved choices are excluded unless
    ``allow_conditional`` is requested.  The chosen exact activations remain in
    the event history for auditability.
    """

    reserve = normalize_mana_bundle(reserve)
    starting_pool = normalize_mana_bundle(starting_pool)
    starting_snow_pool = normalize_mana_bundle(starting_snow_pool)
    if type(snow_required) is not int or snow_required < 0:
        raise ManaPlanError("Snow-mana requirement must be nonnegative")
    if any(
        starting_snow_pool[color] > starting_pool[color]
        for color in MANA_COLORS
    ):
        raise ManaPlanError("Snow-mana provenance exceeds the starting pool")
    fixed_need = normalize_mana_bundle(None)
    for color in MANA_COLORS:
        fixed_need[color] = int(requirements.get(color, 0))
    generic_need = int(requirements.get("GENERIC", 0))

    # DFS chooses at most one mode per source.  Commander boards are small enough
    # for this bounded search; pruning keeps it practical.
    grouped: list[tuple[ManaSource, list[ManaMode]]] = []
    for source in sources:
        modes = [
            mode
            for mode in source.modes
            if not mode.requires_choice and (allow_conditional or not mode.conditional)
        ]
        if modes:
            grouped.append((source, modes))
    grouped.sort(key=lambda pair: (len(pair[1]), -max(mode.total for mode in pair[1]), pair[0].ref))

    best = _select_payment_sources(
        grouped,
        starting_pool=starting_pool,
        starting_snow_pool=starting_snow_pool,
        fixed_need=fixed_need,
        generic_need=generic_need,
        reserve=reserve,
        snow_required=snow_required,
    )
    if best is None:
        raise ManaPlanError("No conservative mana plan can satisfy the declared cost")

    pool = normalize_mana_bundle(starting_pool)
    snow_pool = normalize_mana_bundle(starting_snow_pool)
    activations: list[dict[str, Any]] = []
    for source, mode in best:
        for color, amount in mode.bundle.items():
            pool[color] += amount
            if source.snow:
                snow_pool[color] += amount
        activations.append(
            {
                "source": source.object_id,
                "source_ref": source.ref,
                "bundle": {k: v for k, v in mode.bundle.items() if v},
                "side_effects": list(mode.side_effects),
                "conditional": mode.conditional,
                "restriction": mode.restriction,
            }
        )

    snow_payment = _snow_payment_option(
        pool,
        snow_pool,
        fixed_need=fixed_need,
        generic_need=generic_need,
        reserve=reserve,
        snow_required=snow_required,
    )
    if snow_payment is None:
        raise ManaPlanError("Planner could not assign required snow mana")
    for color in MANA_COLORS:
        pool[color] -= snow_payment[color]
    payment = normalize_mana_bundle(None)
    # Pay fixed symbols first.
    for color in MANA_COLORS:
        amount = fixed_need[color]
        if pool[color] < amount:
            raise ManaPlanError(f"Planner internal error: missing {color}")
        pool[color] -= amount
        payment[color] += amount
    # Preserve requested reserves while paying generic.
    for _ in range(generic_need):
        candidates = [
            color for color in MANA_COLORS if pool[color] > reserve[color]
        ]
        if not candidates:
            raise ManaPlanError("Planner could not preserve requested mana reserve")
        color = sorted(candidates, key=lambda c: (pool[c] - reserve[c], c), reverse=True)[0]
        pool[color] -= 1
        payment[color] += 1

    return ManaPlan(
        activations=activations,
        payment=payment,
        snow_payment=snow_payment,
    )
