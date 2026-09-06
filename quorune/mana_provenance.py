from __future__ import annotations

"""Canonical provenance for mana whose source or spend restriction matters."""

from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from .util import normalize_mana_bundle


MANA_PROVENANCE_KEY = "mana_provenance_v1"
_RESTRICTED_MANA_KEY = "restricted_mana"
_COLORS = "WUBRGC"


class ManaProvenanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManaProvenanceLot:
    bundle: tuple[tuple[str, int], ...]
    snow: bool = False
    restriction: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, tuple) or any(
            type(color) is not str
            or color not in _COLORS
            or type(amount) is not int
            or amount <= 0
            for color, amount in self.bundle
        ):
            raise ManaProvenanceError(
                "mana provenance lots require positive typed mana entries"
            )
        normalized = normalize_mana_bundle(dict(self.bundle))
        expected = tuple(
            (color, normalized[color])
            for color in _COLORS
            if normalized[color]
        )
        if expected != self.bundle or not expected:
            raise ManaProvenanceError(
                "mana provenance lots require a nonempty canonical bundle"
            )
        if type(self.snow) is not bool:
            raise ManaProvenanceError("mana provenance snow flag is invalid")
        if self.restriction is not None and (
            not isinstance(self.restriction, str)
            or not self.restriction.strip()
            or self.restriction != self.restriction.strip()
        ):
            raise ManaProvenanceError(
                "mana provenance restriction is invalid"
            )
        if not self.snow and self.restriction is None:
            raise ManaProvenanceError(
                "ordinary unrestricted mana does not need a provenance lot"
            )

    @classmethod
    def create(
        cls,
        bundle: Mapping[str, int],
        *,
        snow: bool = False,
        restriction: str | None = None,
    ) -> "ManaProvenanceLot":
        normalized = normalize_mana_bundle(bundle)
        return cls(
            tuple(
                (color, normalized[color])
                for color in _COLORS
                if normalized[color]
            ),
            snow=snow,
            restriction=restriction,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManaProvenanceLot":
        if not isinstance(value, Mapping) or set(value) != {
            "bundle",
            "snow",
            "restriction",
        }:
            raise ManaProvenanceError("mana provenance lot fields are invalid")
        bundle = value["bundle"]
        if not isinstance(bundle, Mapping):
            raise ManaProvenanceError("mana provenance bundle must be an object")
        if any(
            type(color) is not str
            or color not in _COLORS
            or type(amount) is not int
            or amount <= 0
            for color, amount in bundle.items()
        ):
            raise ManaProvenanceError(
                "mana provenance bundle must be canonical positive mana"
            )
        return cls.create(
            bundle,
            snow=value["snow"],
            restriction=value["restriction"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle": dict(self.bundle),
            "snow": self.snow,
            "restriction": self.restriction,
        }


def _legacy_restricted_lots(stats: Mapping[str, Any]) -> tuple[ManaProvenanceLot, ...]:
    raw = stats.get(_RESTRICTED_MANA_KEY) or {}
    if not isinstance(raw, Mapping):
        raise ManaProvenanceError("restricted mana must be an object")
    result = []
    for restriction, bundle in sorted(raw.items()):
        if not isinstance(bundle, Mapping):
            raise ManaProvenanceError(
                "restricted mana bundles must be objects"
            )
        normalized = normalize_mana_bundle(bundle)
        if any(normalized.values()):
            result.append(
                ManaProvenanceLot.create(
                    normalized,
                    restriction=str(restriction),
                )
            )
    return tuple(result)


def mana_provenance_lots(player: Any) -> tuple[ManaProvenanceLot, ...]:
    raw = player.stats.get(MANA_PROVENANCE_KEY)
    if raw is None:
        lots = _legacy_restricted_lots(player.stats)
    else:
        if not isinstance(raw, list):
            raise ManaProvenanceError("mana provenance journal must be an array")
        lots = tuple(
            ManaProvenanceLot.from_dict(value)
            if isinstance(value, Mapping)
            else (_raise_lot_shape())
            for value in raw
        )
        if _restricted_summary(lots) != _restricted_summary(
            _legacy_restricted_lots(player.stats)
        ):
            raise ManaProvenanceError(
                "restricted-mana compatibility summary is stale"
            )
    totals = normalize_mana_bundle(None)
    for lot in lots:
        for color, amount in lot.bundle:
            totals[color] += amount
    pool = normalize_mana_bundle(player.mana_pool)
    if any(totals[color] > pool[color] for color in _COLORS):
        raise ManaProvenanceError(
            "mana provenance exceeds the authoritative mana pool"
        )
    return lots


def _raise_lot_shape() -> ManaProvenanceLot:
    raise ManaProvenanceError("mana provenance entries must be objects")


def _restricted_summary(
    lots: Sequence[ManaProvenanceLot],
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for lot in lots:
        if lot.restriction is None:
            continue
        bundle = result.setdefault(
            lot.restriction,
            normalize_mana_bundle(None),
        )
        for color, amount in lot.bundle:
            bundle[color] += amount
    return {
        restriction: {
            color: amount
            for color, amount in bundle.items()
            if amount
        }
        for restriction, bundle in result.items()
        if any(bundle.values())
    }


def _store_lots(player: Any, lots: Sequence[ManaProvenanceLot]) -> None:
    if lots:
        player.stats[MANA_PROVENANCE_KEY] = [lot.to_dict() for lot in lots]
    else:
        player.stats.pop(MANA_PROVENANCE_KEY, None)
    restricted = _restricted_summary(lots)
    if restricted:
        player.stats[_RESTRICTED_MANA_KEY] = restricted
    else:
        player.stats.pop(_RESTRICTED_MANA_KEY, None)


def add_mana(
    player: Any,
    bundle: Mapping[str, int],
    *,
    snow: bool = False,
    restriction: str | None = None,
) -> None:
    normalized = normalize_mana_bundle(bundle)
    if not any(normalized.values()):
        return
    lots = list(mana_provenance_lots(player))
    pool = normalize_mana_bundle(player.mana_pool)
    for color in _COLORS:
        pool[color] += normalized[color]
    player.mana_pool = pool
    if snow or restriction is not None:
        lot = ManaProvenanceLot.create(
            normalized,
            snow=snow,
            restriction=restriction,
        )
        if lots and (
            lots[-1].snow == lot.snow
            and lots[-1].restriction == lot.restriction
        ):
            merged = normalize_mana_bundle(dict(lots[-1].bundle))
            for color, amount in lot.bundle:
                merged[color] += amount
            lots[-1] = ManaProvenanceLot.create(
                merged,
                snow=lot.snow,
                restriction=lot.restriction,
            )
        else:
            lots.append(lot)
    _store_lots(player, lots)


def mark_existing_mana_restricted(
    player: Any,
    restriction: str,
    bundle: Mapping[str, int],
) -> None:
    normalized = normalize_mana_bundle(bundle)
    if not any(normalized.values()):
        return
    lots = list(mana_provenance_lots(player))
    tracked = normalize_mana_bundle(None)
    for lot in lots:
        for color, amount in lot.bundle:
            tracked[color] += amount
    pool = normalize_mana_bundle(player.mana_pool)
    if any(
        normalized[color] > pool[color] - tracked[color]
        for color in _COLORS
    ):
        raise ManaProvenanceError(
            "restricted mana exceeds untracked authoritative mana"
        )
    lots.append(
        ManaProvenanceLot.create(
            normalized,
            restriction=restriction,
        )
    )
    _store_lots(player, lots)


def restricted_mana(player: Any) -> dict[str, dict[str, int]]:
    return {
        restriction: normalize_mana_bundle(bundle)
        for restriction, bundle in _restricted_summary(
            mana_provenance_lots(player)
        ).items()
    }


def spendable_mana_pool(
    player: Any,
    *,
    restriction_allows: Callable[[str, str | None], bool],
    spend_context: str | None,
) -> dict[str, int]:
    pool = normalize_mana_bundle(player.mana_pool)
    for lot in mana_provenance_lots(player):
        if lot.restriction is None or restriction_allows(
            lot.restriction,
            spend_context,
        ):
            continue
        for color, amount in lot.bundle:
            pool[color] -= amount
    if any(pool[color] < 0 for color in _COLORS):
        raise ManaProvenanceError("spendable mana pool is inconsistent")
    return pool


def snow_mana_pool(
    player: Any,
    *,
    restriction_allows: Callable[[str, str | None], bool],
    spend_context: str | None,
) -> dict[str, int]:
    result = normalize_mana_bundle(None)
    for lot in mana_provenance_lots(player):
        if not lot.snow or (
            lot.restriction is not None
            and not restriction_allows(lot.restriction, spend_context)
        ):
            continue
        for color, amount in lot.bundle:
            result[color] += amount
    return result


def spend_mana(
    player: Any,
    spent: Mapping[str, int],
    *,
    snow_payment: Mapping[str, int] | None,
    restriction_allows: Callable[[str, str | None], bool],
    spend_context: str | None,
) -> None:
    total_spent = normalize_mana_bundle(spent)
    snow_spent = normalize_mana_bundle(snow_payment)
    if any(
        snow_spent[color] > total_spent[color]
        for color in _COLORS
    ):
        raise ManaProvenanceError(
            "snow payment exceeds the selected mana payment"
        )
    lots = [
        {
            "bundle": normalize_mana_bundle(dict(lot.bundle)),
            "snow": lot.snow,
            "restriction": lot.restriction,
        }
        for lot in mana_provenance_lots(player)
    ]
    pool = normalize_mana_bundle(player.mana_pool)
    untracked = {
        color: pool[color] - sum(lot["bundle"][color] for lot in lots)
        for color in _COLORS
    }
    if any(amount < 0 for amount in untracked.values()):
        raise ManaProvenanceError(
            "mana provenance exceeds the authoritative pool"
        )

    def eligible(lot: Mapping[str, Any]) -> bool:
        restriction = lot["restriction"]
        return restriction is None or restriction_allows(
            restriction,
            spend_context,
        )

    def consume(color: str, amount: int, *, snow: bool) -> int:
        remaining = amount
        for lot in lots:
            if remaining <= 0:
                break
            if bool(lot["snow"]) is not snow or not eligible(lot):
                continue
            bundle = lot["bundle"]
            use = min(remaining, bundle[color])
            bundle[color] -= use
            remaining -= use
        return amount - remaining

    for color in _COLORS:
        required_snow = snow_spent[color]
        if consume(color, required_snow, snow=True) != required_snow:
            raise ManaProvenanceError(
                "selected snow payment is not available"
            )
        remaining = total_spent[color] - required_snow
        used_nonsnow = consume(color, remaining, snow=False)
        remaining -= used_nonsnow
        use_untracked = min(remaining, untracked[color])
        untracked[color] -= use_untracked
        remaining -= use_untracked
        if remaining:
            used_snow = consume(color, remaining, snow=True)
            remaining -= used_snow
        if remaining:
            raise ManaProvenanceError(
                "mana payment exceeds eligible provenance"
            )
        pool[color] -= total_spent[color]
        if pool[color] < 0:
            raise ManaProvenanceError(
                "mana payment exceeds the authoritative pool"
            )
    remaining_lots = []
    for raw in lots:
        if not any(raw["bundle"].values()):
            continue
        remaining_lots.append(
            ManaProvenanceLot.create(
                raw["bundle"],
                snow=raw["snow"],
                restriction=raw["restriction"],
            )
        )
    player.mana_pool = pool
    _store_lots(player, remaining_lots)


def clear_mana_provenance(stats: MutableMapping[str, Any]) -> None:
    stats.pop(MANA_PROVENANCE_KEY, None)
    stats.pop(_RESTRICTED_MANA_KEY, None)


def _mark_uncounterable_spell_payment(player: Any) -> None:
    player.stats["next_spell_uncounterable"] = True


class ManaProvenanceHostMixin:
    """Narrow engine facade for the typed mana-provenance state owner."""

    state: Any

    def _restricted_mana(self, seat: str) -> dict[str, dict[str, int]]:
        return restricted_mana(self.state.players[seat])

    def _add_restricted_mana(
        self,
        seat: str,
        restriction: str,
        bundle: Mapping[str, int],
    ) -> None:
        mark_existing_mana_restricted(
            self.state.players[seat],
            restriction,
            bundle,
        )

    def _add_mana_to_pool(
        self,
        seat: str,
        bundle: Mapping[str, int],
        *,
        restriction: str | None = None,
        snow_source: bool = False,
    ) -> None:
        add_mana(
            self.state.players[seat],
            bundle,
            snow=snow_source,
            restriction=restriction,
        )

    def _mana_source_is_snow(self, source_ref: str) -> bool:
        card = next(
            (
                value
                for value in self.state.cards.values()
                if value.ref == source_ref
            ),
            None,
        )
        if card is None:
            item = next(
                (
                    value
                    for value in self.state.stack
                    if value.ref == source_ref
                ),
                None,
            )
            object_id = (
                item.source_object_id or item.card_object_id
                if item is not None
                else None
            )
            card = self.state.cards.get(object_id or "")
        if card is None:
            return False
        try:
            type_line = str(
                self._effective_card_data(card).get("type_line") or ""
            )
            return "snow" in self._type_parts(type_line)[2]
        except (KeyError, TypeError, ValueError):
            return False

    def _spendable_mana_pool(
        self,
        seat: str,
        spend_context: str | None,
    ) -> dict[str, int]:
        return spendable_mana_pool(
            self.state.players[seat],
            restriction_allows=self._mana_restriction_allows,
            spend_context=spend_context,
        )

    def _snow_mana_pool(
        self,
        seat: str,
        spend_context: str | None,
    ) -> dict[str, int]:
        return snow_mana_pool(
            self.state.players[seat],
            restriction_allows=self._mana_restriction_allows,
            spend_context=spend_context,
        )

    def _apply_mana_spend(
        self,
        seat: str,
        spent: Mapping[str, int],
        spend_context: str | None,
        *,
        snow_payment: Mapping[str, int] | None = None,
    ) -> None:
        before = self._restricted_mana(seat)
        spend_mana(
            self.state.players[seat],
            spent,
            snow_payment=snow_payment,
            restriction_allows=self._mana_restriction_allows,
            spend_context=spend_context,
        )
        after = self._restricted_mana(seat)
        if sum(
            before.get("legendary_spell_uncounterable", {}).values()
        ) > sum(
            after.get("legendary_spell_uncounterable", {}).values()
        ):
            _mark_uncounterable_spell_payment(self.state.players[seat])


__all__ = [
    "MANA_PROVENANCE_KEY",
    "ManaProvenanceError",
    "ManaProvenanceHostMixin",
    "ManaProvenanceLot",
    "add_mana",
    "clear_mana_provenance",
    "mana_provenance_lots",
    "mark_existing_mana_restricted",
    "restricted_mana",
    "snow_mana_pool",
    "spend_mana",
    "spendable_mana_pool",
]
