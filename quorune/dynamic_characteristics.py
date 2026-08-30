from __future__ import annotations

from typing import Any, Mapping, Protocol

from .ability_fragments import canonical_ability_fragments
from .characteristic_fragments import (
    CharacteristicCountKind,
    ConditionalKeywordSpec,
    DynamicPowerToughnessSpec,
    PowerToughnessCalculation,
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from .continuous_effects import Layer
from .object_query import object_matches_query, object_query_result
from .util import unique_preserving_order


class DynamicCharacteristicHost(Protocol):
    state: Any

    def _copyable_characteristics(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _effective_card_data(
        self,
        card: Any,
        *,
        maximum_layer: Layer | None = None,
        _enforce_static_component_applicability: bool = True,
    ) -> Mapping[str, Any]: ...


def query_characteristic_count(
    host: DynamicCharacteristicHost,
    source: Any,
    quantity: CharacteristicQuantitySpec,
    *,
    _enforce_static_component_applicability: bool = True,
) -> int:
    """Resolve one closed public quantity through layer 5 only."""

    if quantity.scope is CharacteristicQuantityScope.SOURCE_COUNTER:
        assert quantity.counter_name is not None
        raw = source.counters.get(quantity.counter_name, 0)
        if type(raw) is not int or raw < 0:
            raise ValueError(
                "Characteristic source counters must be nonnegative integers"
            )
        return raw

    assert quantity.query is not None
    zone = quantity.query.zones[0]
    controller = source.controller
    if quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE:
        object_ids = tuple(source.attachments)
    elif quantity.scope is CharacteristicQuantityScope.CONTROLLER_ZONE:
        object_ids = tuple(host.state.players[controller].zones[zone])
    elif quantity.scope is CharacteristicQuantityScope.OPPONENT_ZONES:
        object_ids = tuple(
            object_id
            for seat, player in host.state.players.items()
            if seat != controller and player.in_game
            for object_id in player.zones[zone]
        )
    else:
        object_ids = tuple(
            object_id
            for player in host.state.players.values()
            for object_id in player.zones[zone]
        )

    if zone == "hand":
        # The closed hand grammar carries no identity predicates. Counting the
        # zone directly keeps hidden card characteristics outside this owner.
        return len(object_ids)

    count = 0
    for object_id in object_ids:
        if object_id not in host.state.cards:
            continue
        candidate = host.state.cards[object_id]
        if quantity.exclude_source and candidate.ref == source.ref:
            continue
        if (
            quantity.exclude_attached_object
            and candidate.object_id == getattr(source, "attached_to", None)
        ):
            continue
        effective = host._effective_card_data(
            candidate,
            maximum_layer=Layer.COLOR,
            _enforce_static_component_applicability=(
                _enforce_static_component_applicability
            ),
        )
        attached = (
            host.state.cards.get(candidate.attached_to)
            if candidate.attached_to is not None
            else None
        )
        row = object_query_result(
            candidate,
            effective,
            type_parts=host._type_parts(
                str(effective.get("type_line") or "")
            ),
            known_to_actor=True,
            attached_to_ref=attached.ref if attached is not None else None,
        )
        if object_matches_query(row, quantity.query):
            count += 1
    return count


def _has_card_type(
    host: DynamicCharacteristicHost,
    card: Any,
    card_type: str,
) -> bool:
    copyable = host._copyable_characteristics(card)
    return card_type in host._type_parts(
        str(copyable.get("type_line") or "")
    )[0]


def _matching_count(
    host: DynamicCharacteristicHost,
    card: Any,
    kind: CharacteristicCountKind,
) -> int:
    if kind is CharacteristicCountKind.CONTROLLER_BATTLEFIELD_ARTIFACTS:
        object_ids = host.state.players[card.controller].zones["battlefield"]
        return sum(
            1
            for object_id in object_ids
            if not host.state.cards[object_id].phased_out
            and _has_card_type(
                host,
                host.state.cards[object_id],
                "artifact",
            )
        )
    object_ids = host.state.players[card.owner].zones["graveyard"]
    card_type = (
        "creature"
        if kind
        is CharacteristicCountKind.OWNER_GRAVEYARD_CREATURE_CARDS
        else "land"
    )
    return sum(
        1
        for object_id in object_ids
        if _has_card_type(host, host.state.cards[object_id], card_type)
    )


def _modify_power_toughness(
    result: dict[str, Any],
    *,
    power: int,
    toughness: int,
    multiplier: int,
) -> None:
    for field, amount in (("power", power), ("toughness", toughness)):
        try:
            result[field] = str(
                int(str(result.get(field))) + amount * multiplier
            )
        except (TypeError, ValueError):
            continue


def apply_dynamic_characteristic_fragments(
    host: DynamicCharacteristicHost,
    card: Any,
    characteristics: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply compiled live-state fragments without interpreting rules prose."""

    result = dict(characteristics)
    if card.zone != "battlefield" or card.phased_out:
        return result
    fragments = canonical_ability_fragments(
        result.get("ability_fragments", ())
    )
    for fragment in fragments:
        if isinstance(fragment, ConditionalKeywordSpec):
            if any(
                seat != card.controller
                and player.in_game
                and player.life <= fragment.opponent_life_at_most
                for seat, player in host.state.players.items()
            ):
                result["keywords"] = unique_preserving_order(
                    [*result.get("keywords", ()), fragment.keyword]
                )
            continue
        if not isinstance(fragment, DynamicPowerToughnessSpec):
            continue
        count = _matching_count(host, card, fragment.count_kind)
        multiplier = (
            count
            if fragment.calculation
            is PowerToughnessCalculation.PER_MATCHING_OBJECT
            else int(count >= fragment.minimum_count)
        )
        _modify_power_toughness(
            result,
            power=fragment.power,
            toughness=fragment.toughness,
            multiplier=multiplier,
        )
    return result


__all__ = [
    "DynamicCharacteristicHost",
    "apply_dynamic_characteristic_fragments",
    "query_characteristic_count",
]
