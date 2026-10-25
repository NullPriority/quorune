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
    CharacteristicQuantityReduction,
)
from .continuous_effects import Layer
from .object_query import object_matches_query, object_query_result
from .public_quantity_reductions import reduce_public_quantity
from .counter_snapshot import permanent_counter_total
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
    if quantity.schema_version==5:
        from .public_zone_quantities import current_public_zone_quantity
        return current_public_zone_quantity(host,source,quantity,enforce_applicability=_enforce_static_component_applicability)

    if quantity.schema_version == 4:
        from .rules.live_modifier_quantities import current_modifier_quantity
        return current_modifier_quantity(host,source,quantity,enforce_applicability=_enforce_static_component_applicability)
    if quantity.schema_version == 3:
        return sum(query_characteristic_count(host, source, term,
                   _enforce_static_component_applicability=_enforce_static_component_applicability)
                   for term in quantity.terms) * quantity.multiplier + quantity.offset
    if quantity.scope is CharacteristicQuantityScope.CONTROLLER_OPPONENT_COUNT:
        return current_opponent_count(host.state,source.controller) * quantity.multiplier + quantity.offset
    if quantity.reduction is CharacteristicQuantityReduction.DEVOTION:
        from .devotion import current_devotion
        return current_devotion(host,source.controller,quantity.mana_colors,
            include_modifiers=_enforce_static_component_applicability,
            _enforce_static_component_applicability=_enforce_static_component_applicability) * quantity.multiplier + quantity.offset
    if quantity.scope is CharacteristicQuantityScope.SOURCE_COUNTER:
        if quantity.reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS:
            return permanent_counter_total(source.counters) * quantity.multiplier + quantity.offset
        assert quantity.counter_name is not None
        raw = source.counters.get(quantity.counter_name, 0)
        if type(raw) is not int or raw < 0:
            raise ValueError(
                "Characteristic source counters must be nonnegative integers"
            )
        return raw * quantity.multiplier + quantity.offset

    assert quantity.query is not None
    zone = quantity.query.zones[0]
    controller = source.controller
    if quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE:
        object_ids = tuple(source.attachments)
    elif quantity.scope is CharacteristicQuantityScope.ATTACHED_TO_RECIPIENT:
        from .attachments import attached_object_identity
        identity = attached_object_identity(host.state.cards, source)
        if identity is None:
            return 0
        recipient = host.state.cards[identity.object_id]
        object_ids = tuple(object_id for object_id in dict.fromkeys(recipient.attachments)
            if object_id in host.state.cards
            and host.state.cards[object_id].attached_to == recipient.object_id)
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
        return len(object_ids) * quantity.multiplier + quantity.offset

    matched = []
    for object_id in object_ids:
        if object_id not in host.state.cards:
            if quantity.schema_version == 2:
                raise ValueError("A public collection member is unavailable")
            continue
        candidate = host.state.cards[object_id]
        if (quantity.schema_version == 2 or quantity.query.state_predicate is not None) and candidate.phased_out:
            continue
        if quantity.schema_version == 2 and quantity.query.zones == ("graveyard",) and quantity.query.token is False and candidate.object_kind != "card":
            continue
        if quantity.exclude_source and candidate.ref == source.ref:
            continue
        if (
            quantity.exclude_attached_object
            and candidate.object_id == getattr(source, "attached_to", None)
        ):
            continue
        effective = host._effective_card_data(
            candidate,
            maximum_layer=Layer.TEXT if quantity.reduction is CharacteristicQuantityReduction.DEVOTION else Layer.COLOR,
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
            effective if quantity.schema_version == 1 else {**effective, "mana_value": 0},
            type_parts=host._type_parts(
                str(effective.get("type_line") or "")
            ),
            known_to_actor=True,
            attached_to_ref=attached.ref if attached is not None else None,
            **_quantity_attachment_state(host, candidate, quantity,
                enforce_applicability=_enforce_static_component_applicability),
        )
        if object_matches_query(row, quantity.query):
            matched.append((row, effective, candidate.object_kind if quantity.schema_version == 2 else ""))
    return reduce_public_quantity(matched, quantity.reduction, mana_colors=quantity.mana_colors) * quantity.multiplier + quantity.offset


def _quantity_attachment_state(host, candidate, quantity, *, enforce_applicability):
    predicate = quantity.query.state_predicate
    if predicate is None or all(getattr(predicate, field) is None for field in ('enchanted', 'equipped', 'modified')):
        return {}
    enchanted, equipped, modified = host._attachment_public_state(candidate,
        _enforce_static_component_applicability=enforce_applicability)
    return {'enchanted': enchanted, 'equipped': equipped, 'modified': modified}


def current_opponent_count(state,controller: str) -> int:
    """Count active opponents in the represented free-for-all roster."""
    if controller not in state.players or any(type(player.in_game) is not bool for player in state.players.values()):
        raise ValueError('A public opponent quantity requires a known controller and current roster')
    return sum(player.in_game for seat,player in state.players.items() if seat!=controller)


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
