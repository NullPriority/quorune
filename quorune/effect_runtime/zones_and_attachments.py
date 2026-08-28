from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from ..attachments import attach_objects
from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ContinuousEffectStateError,
    create_resolution_continuous_effect,
    matching_battlefield_objects,
    resolution_effect_source,
)
from ..destruction import destroy_permanent_refs
from ..errors import GameRuleError
from ..effect_contracts import (
    effect_family_contract,
    REANIMATE_OPERATION,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..model import CardInstance
from ..milling import mill_cards, MillRequest
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..targets import TargetGroup
from ..util import unique_preserving_order

_EXILE_ZONE = "exile"


OPERATIONS = effect_family_contract("zones-and-attachments.v1").operations


def _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    implicit_zones = {
        "sacrifice": {"battlefield"},
        "destroy": {"battlefield"},
        "bounce": {"battlefield"},
        "discard": {"hand"},
    }
    card = host._resolve_object(
        actor,
        str(effect["card"]),
        zones=implicit_zones.get(op),
    )
    if op == "destroy":
        result = destroy_permanent_refs(
            host,
            (card.ref,),
            actor=actor,
            reason=reason,
            replacement_selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
        )
        return card if result.destroyed_object_ids else None
    destination = {
        "sacrifice": "graveyard",
        "destroy": "graveyard",
        "exile": "exile",
        "bounce": "hand",
        "discard": "graveyard",
    }.get(op, str(effect.get("destination") or "graveyard"))
    return host.move_card(
        card.object_id,
        destination,
        controller=effect.get("controller"),
        tapped=(
            bool(effect["tapped"])
            if "tapped" in effect
            else None
        ),
        position=(
            effect["position"]
            if effect.get("position") is not None
            else "top"
        ),
        reason=reason,
        semantic_events=True,
        replacement_selections=tuple(
            effect.get("_replacement_selections") or ()
        ),
        aura_target_ref=(
            str(effect["_aura_target_ref"])
            if effect.get("_aura_target_ref") is not None
            else None
        ),
    )



def _apply_move_if_in_zone(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    expected_zone = str(effect.get("from") or "")
    try:
        card = host._resolve_object(
            actor,
            str(effect["card"]),
        )
    except GameRuleError:
        host._log(
            actor,
            "effect.linked_object_missing",
            "A linked object no longer exists in the game.",
            {
                "object": str(effect["card"]),
                "expected_zone": expected_zone,
                "reason": "object_absent",
            },
            importance=2,
        )
        return None
    if not expected_zone or card.zone != expected_zone:
        return None
    expected_counter = effect.get(
        "expected_zone_change_counter"
    )
    expected_identity = effect.get(
        "expected_object_identity"
    )
    identity_mismatch = (
        expected_counter is not None
        and card.zone_change_counter
        != int(expected_counter)
    ) or (
        expected_identity is not None
        and card.logical_object_id
        != str(expected_identity)
    )
    if identity_mismatch:
        host._log(
            actor,
            "effect.linked_object_missing",
            (
                f"{card.ref} is no longer the object linked by "
                "the effect."
            ),
            {
                "object": card.ref,
                "expected_zone": expected_zone,
                "reason": "object_identity_changed",
            },
            importance=2,
            changed_objects=[card.object_id],
        )
        return None
    return host.move_card(
        card.object_id,
        str(effect.get("destination") or "graveyard"),
        controller=effect.get("controller"),
        tapped=(
            bool(effect["tapped"])
            if "tapped" in effect
            else None
        ),
        position=(
            effect["position"]
            if effect.get("position") is not None
            else "top"
        ),
        reason=reason,
        semantic_events=True,
        replacement_selections=tuple(
            effect.get("_replacement_selections") or ()
        ),
        aura_target_ref=(
            str(effect["_aura_target_ref"])
            if effect.get("_aura_target_ref") is not None
            else None
        ),
    )



def _apply_prepare_graveyard_creature_aura(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    aura = host._resolve_object(
        actor,
        str(effect["aura"]),
        zones={"stack"},
    )
    target = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"graveyard"},
    )
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(target).get(
                "type_line"
            )
            or ""
        )
    )
    if "creature" not in types:
        raise GameRuleError(
            "The Aura requires a creature card in a graveyard"
        )
    aura.annotations["pending_aura_target"] = target.ref
    return target.ref



def _apply_bestow_prepare(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    aura = host._resolve_object(
        actor,
        str(effect["aura"]),
        zones={"stack"},
    )
    target = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"battlefield"},
    )
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(target).get(
                "type_line"
            )
            or ""
        )
    )
    if "creature" not in types:
        raise GameRuleError(
            "Bestow requires a creature target"
        )
    aura.annotations["bestowed"] = True
    aura.annotations["pending_aura_target"] = target.ref
    aura.annotations["pending_aura_zone"] = "battlefield"
    return target.ref



def _apply_reanimate_attached_creature_aura(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    aura = host._resolve_object(
        actor,
        str(effect["aura"]),
        zones={"battlefield"},
        controlled_only=True,
    )
    target_id = aura.attached_to
    if target_id not in host.state.cards:
        return None
    creature = host.state.cards[target_id]
    if creature.zone != "graveyard":
        return None
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(creature).get(
                "type_line"
            )
            or ""
        )
    )
    if "creature" not in types:
        return None
    host.move_card(
        creature.object_id,
        "battlefield",
        controller=actor,
        reason=reason,
        semantic_events=True,
    )
    attach_objects(
        host.state.cards,
        aura,
        creature,
        source_timestamp=host._next_zone_timestamp(),
        players=host.state.players,
    )
    link_annotation = str(
        effect.get("link_annotation") or "reanimated_creature"
    )
    aura.annotations[link_annotation] = creature.object_id
    host._log(
        actor,
        str(effect.get("event_code") or "aura.reanimate"),
        (
            f"{actor} returned {creature.ref} and attached "
            f"{aura.ref} to it."
        ),
        {
            "aura": aura.ref,
            "creature": creature.ref,
        },
        importance=2,
        changed_objects=[aura.object_id, creature.object_id],
        changed_players=[actor],
    )
    return creature.ref



def _apply_attach(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    equipment_value = str(
        effect.get("equipment") or effect.get("source")
    )
    creature_value = str(effect["creature"])

    def attachment_object(value: str) -> CardInstance:
        identity_matches = [
            card
            for card in host.state.cards.values()
            if card.object_id == value
            or card.ref.casefold() == value.casefold()
        ]
        if len(identity_matches) == 1:
            return identity_matches[0]
        return host._resolve_object(
            actor,
            value,
            zones={"battlefield"},
        )

    equipment = attachment_object(equipment_value)
    if equipment.zone != "battlefield":
        host._log(
            actor,
            "attachment.no_effect",
            (
                f"{equipment.ref} could not become attached because "
                "it was no longer on the battlefield."
            ),
            {
                "equipment": equipment.ref,
                "creature": creature_value,
                "reason": reason,
                "result": "equipment_not_on_battlefield",
            },
            importance=2,
        )
        return None
    creature = attachment_object(creature_value)
    if creature.zone != "battlefield":
        host._log(
            actor,
            "attachment.no_effect",
            (
                f"{equipment.ref} could not become attached because "
                f"{creature.ref} was no longer on the battlefield."
            ),
            {
                "equipment": equipment.ref,
                "creature": creature.ref,
                "reason": reason,
                "result": "creature_not_on_battlefield",
            },
            importance=2,
        )
        return None
    equipment_types, equipment_subtypes, _ = host._type_parts(
        str(
            host._effective_card_data(equipment).get("type_line")
            or ""
        )
    )
    creature_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(creature).get("type_line")
            or ""
        )
    )
    if (
        "artifact" not in equipment_types
        or "equipment" not in equipment_subtypes
        or "creature" not in creature_types
    ):
        raise GameRuleError(
            "Attach requires an Equipment and a creature"
        )
    attach_objects(
        host.state.cards,
        equipment,
        creature,
        source_timestamp=(
            equipment.zone_timestamp
            if equipment.attached_to == creature.object_id
            else host._next_zone_timestamp()
        ),
        players=host.state.players,
    )
    host._log(
        actor,
        "attachment.attach",
        f"{equipment.ref} became attached to {creature.ref}.",
        {
            "equipment": equipment.ref,
            "creature": creature.ref,
            "reason": reason,
        },
        importance=2,
        changed_objects=[
            equipment.object_id,
            creature.object_id,
        ],
    )
    return creature.ref



def _apply_exchange_artifact_zones(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    battlefield_ref = effect.get("battlefield_card")
    graveyard_ref = effect.get("graveyard_card")
    if not battlefield_ref or not graveyard_ref:
        return None
    try:
        battlefield_card = host._resolve_object(
            actor,
            str(battlefield_ref),
            zones={"battlefield"},
        )
        graveyard_card = host._resolve_object(
            actor,
            str(graveyard_ref),
            zones={"graveyard"},
        )
    except GameRuleError:
        return None
    battlefield_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(battlefield_card).get(
                "type_line"
            )
            or ""
        )
    )
    graveyard_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(graveyard_card).get(
                "type_line"
            )
            or ""
        )
    )
    if (
        "artifact" not in battlefield_types
        or "artifact" not in graveyard_types
        or battlefield_card.controller != graveyard_card.owner
    ):
        return None
    host._move_cards_simultaneously(
        [
            (battlefield_card.object_id, "graveyard"),
            (graveyard_card.object_id, "battlefield"),
        ],
        reason=reason,
        log=True,
    )
    return [battlefield_card.ref, graveyard_card.ref]



def _apply_mill(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    del operation
    count = max(0, int(effect.get("count", 1)))
    if not count:
        return []
    result = mill_cards(
        host,
        MillRequest(
            actor=actor,
            player=str(effect.get("player") or actor),
            count=count,
            reason=reason,
        ),
    )
    return list(result.refs)



def _apply_reanimate(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    card = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"graveyard"},
    )
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(card).get("type_line")
            or ""
        )
    )
    if "creature" not in types:
        raise GameRuleError(
            "Reanimate effect requires a creature card"
        )
    return host.move_card(
        card.object_id,
        "battlefield",
        controller=str(effect.get("controller") or actor),
        reason=reason,
        semantic_events=True,
    )



def _apply_exile_all(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    specification = dict(effect.get("filter") or {})
    specification.setdefault("zones", ["battlefield"])
    specification.setdefault("categories", ["permanent"])
    specification.setdefault("min", 0)
    specification.setdefault("max", 10000)
    group = TargetGroup.from_mapping(
        specification,
        default_id="affected",
    )
    source_ref = str(effect.get("source") or "") or None
    refs = [
        str(row["ref"])
        for row in host._target_candidate_rows(actor, group)
        if host._target_row_matches(
            actor,
            group,
            row,
            source_ref=source_ref,
            as_target=False,
        )
    ]
    cards: list[CardInstance] = []
    for ref in refs:
        try:
            card = host._resolve_object(
                actor, ref, zones={"battlefield"}
            )
        except GameRuleError:
            continue
        cards.append(card)
    changes = [(card.object_id, _EXILE_ZONE) for card in cards]
    host._move_cards_simultaneously(changes, reason=reason, log=True)
    return [host.state.cards[object_id].ref for object_id, _ in changes]



def _apply_exile_opponent_graveyards(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    changes: list[tuple[str, str]] = []
    for opponent in host.active_seats:
        if opponent == actor:
            continue
        for object_id in list(
            host.state.players[opponent].zones["graveyard"]
        ):
            changes.append((object_id, "exile"))
    host._move_cards_simultaneously(
        changes,
        reason=reason,
        log=True,
    )
    return [host.state.cards[object_id].ref for object_id, _ in changes]



def _apply_exile_graveyard(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    player = str(effect["player"])
    if player not in host.active_seats:
        raise GameRuleError(
            "Graveyard exile requires an active player"
        )
    changes = [
        (object_id, "exile")
        for object_id in list(
            host.state.players[player].zones["graveyard"]
        )
    ]
    host._move_cards_simultaneously(
        changes,
        reason=reason,
        log=True,
    )
    return [
        host.state.cards[object_id].ref
        for object_id, _ in changes
    ]



def _apply_destroy_selected(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    refs: list[str] = []
    for raw_ref in effect.get("cards") or []:
        if raw_ref is None:
            continue
        try:
            card = host._resolve_object(
                actor,
                str(raw_ref),
                zones={"battlefield"},
            )
        except GameRuleError:
            continue
        refs.append(card.ref)
    result = destroy_permanent_refs(
        host,
        refs,
        actor=actor,
        reason=reason,
    )
    return [
        host.state.cards[object_id].ref
        for object_id in result.destroyed_object_ids
    ]



def _fixed_characteristic_keywords(
    effect: Mapping[str, Any],
) -> tuple[str, ...]:
    if "keywords" not in effect:
        return ()
    raw_keywords = effect["keywords"]
    if not isinstance(raw_keywords, list):
        raise GameRuleError("Fixed characteristic keywords must be a list")
    keywords = tuple(raw_keywords)
    if (
        not keywords
        or any(type(keyword) is not str for keyword in keywords)
        or len(set(keywords)) != len(keywords)
        or any(
            keyword not in FIXED_CHARACTERISTIC_KEYWORDS
            for keyword in keywords
        )
    ):
        raise GameRuleError(
            "Fixed characteristic keywords must be unique supported keywords"
        )
    return keywords


def _apply_modify_all_matching_permanents_until_end_of_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    keywords = _fixed_characteristic_keywords(effect)
    raw_predicate = effect.get("predicate")
    if raw_predicate is not None:
        try:
            predicate = ObjectQuerySpec.from_dict(raw_predicate)
        except ObjectQueryError as exc:
            raise GameRuleError(str(exc)) from exc
        power_delta = int(effect.get("power", 0))
        toughness_delta = int(effect.get("toughness", 0))
    else:
        amount = int(effect.get("amount", 0))
        scale = int(effect.get("scale", 1))
        required_type = str(
            effect.get("required_type") or ""
        ).casefold()
        if amount < 0 or not required_type:
            raise GameRuleError("Mass modification parameters are invalid")
        power_delta = int(effect.get("power_delta", 0)) + amount * scale
        toughness_delta = (
            int(effect.get("toughness_delta", 0)) + amount * scale
        )
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(required_type,),
        )
    try:
        affected = matching_battlefield_objects(host, predicate)
    except ContinuousEffectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    affected_ids = [card.object_id for card in affected]
    if host.state.continuous_effects is not None:
        try:
            source = resolution_effect_source(host, effect)
            if (
                "keywords" not in effect
                or power_delta
                or toughness_delta
            ):
                create_resolution_continuous_effect(
                    host,
                    source=source,
                    targets=affected,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness",
                            [power_delta, toughness_delta],
                        ),
                    ),
                )
            if keywords:
                create_resolution_continuous_effect(
                    host,
                    source=source,
                    targets=affected,
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=tuple(
                        ContinuousOperation("add_ability", keyword)
                        for keyword in keywords
                    ),
                )
        except ContinuousEffectStateError as exc:
            raise GameRuleError(str(exc)) from exc
    else:
        for card in affected:
            until_end = card.annotations.setdefault(
                "until_end_of_turn", {}
            )
            until_end["power"] = (
                int(until_end.get("power", 0)) + power_delta
            )
            until_end["toughness"] = (
                int(until_end.get("toughness", 0)) + toughness_delta
            )
            card.temporary_keywords = unique_preserving_order(
                [*card.temporary_keywords, *keywords]
            )
    host._log(
        actor,
        str(effect.get("event_code") or "effect.mass_modify"),
        (
            f"Matching permanents got {power_delta:+d}/{toughness_delta:+d} "
            "until end of turn."
        ),
        {
            "amount": effect.get("amount"),
            "power_delta": power_delta,
            "toughness_delta": toughness_delta,
            "keywords": list(keywords),
            "objects": [
                host.state.cards[object_id].ref
                for object_id in affected_ids
            ],
        },
        importance=2,
        changed_objects=affected_ids,
    )
    return [
        host.state.cards[object_id].ref
        for object_id in affected_ids
    ]



def _apply_pump_controlled_creatures(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    amount = int(effect.get("amount", 0))
    minimum = int(effect.get("minimum_amount", 0))
    if amount < minimum:
        return []
    keywords = [
        str(value)
        for value in effect.get("keywords", [])
    ]
    predicate = ObjectQuerySpec(
        zones=("battlefield",),
        controller=actor,
        types_all=("creature",),
    )
    affected = matching_battlefield_objects(host, predicate)
    changed = [card.object_id for card in affected]
    if host.state.continuous_effects is not None:
        try:
            if amount:
                create_resolution_continuous_effect(
                    host,
                    source=resolution_effect_source(host, effect),
                    targets=affected,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness", [amount, amount]
                        ),
                    ),
                )
            if keywords:
                create_resolution_continuous_effect(
                    host,
                    source=resolution_effect_source(host, effect),
                    targets=affected,
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=tuple(
                        ContinuousOperation("add_ability", keyword)
                        for keyword in keywords
                    ),
                )
        except ContinuousEffectStateError as exc:
            raise GameRuleError(str(exc)) from exc
    else:
        for card in affected:
            until_end = card.annotations.setdefault(
                "until_end_of_turn", {}
            )
            until_end["power"] = int(
                until_end.get("power", 0)
            ) + amount
            until_end["toughness"] = int(
                until_end.get("toughness", 0)
            ) + amount
            card.temporary_keywords = unique_preserving_order(
                [*card.temporary_keywords, *keywords]
            )
    host._log(
        actor,
        "effect.creature_pump",
        f"{actor}'s creatures got +{amount}/+{amount}.",
        {
            "amount": amount,
            "keywords": keywords,
            "objects": [
                host.state.cards[object_id].ref
                for object_id in changed
            ],
        },
        importance=2,
        changed_objects=changed,
        changed_players=[actor],
    )
    return [
        host.state.cards[object_id].ref
        for object_id in changed
    ]



def _apply_shuffle_into_library(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    card = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={
            "battlefield",
            "graveyard",
            "exile",
            "stack",
        },
    )
    owner = card.owner
    moved = host.move_card(
        card.object_id,
        "library",
        reason=reason,
        semantic_events=True,
    )
    host.shuffle_library(owner, reason=reason)
    return moved.ref



def _apply_shuffle_graveyard_bottom_random(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    raw_players = effect.get("players")
    players = (
        [str(value) for value in raw_players]
        if isinstance(raw_players, Sequence)
        and not isinstance(raw_players, (str, bytes))
        else (
            [str(raw_players)]
            if raw_players
            else []
        )
    )
    moved_by_player: dict[str, list[str]] = {}
    for seat in players:
        if seat not in host.active_seats:
            continue
        graveyard_ids = list(
            host.state.players[seat].zones["graveyard"]
        )
        if not graveyard_ids:
            moved_by_player[seat] = []
            continue
        randomizer = random.Random(
            f"{host.state.config.seed}|{host.state.turn_sequence}|"
            f"{seat}|graveyard-bottom|{host.state.event_sequence}"
        )
        randomizer.shuffle(graveyard_ids)
        host._move_cards_simultaneously(
            [
                (object_id, "library")
                for object_id in graveyard_ids
            ],
            reason=reason,
            log=False,
        )
        library = host.state.players[seat].zones["library"]
        moved_set = set(graveyard_ids)
        remaining_ids = [
            object_id
            for object_id in library
            if object_id not in moved_set
        ]
        library[:] = [*graveyard_ids, *remaining_ids]
        moved_by_player[seat] = [
            host.state.cards[object_id].ref
            for object_id in graveyard_ids
        ]
        host._log(
            actor,
            "graveyard.bottom",
            (
                f"{seat} put {len(graveyard_ids)} graveyard "
                "card(s) on the bottom in a random order."
            ),
            {
                "player": seat,
                "count": len(graveyard_ids),
                "reason": reason,
            },
            importance=2,
            changed_objects=graveyard_ids,
            changed_players=[seat],
        )
    return moved_by_player



def _apply_reveal_top_permanent(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    library = host.state.players[seat].zones["library"]
    if not library:
        return None
    card = host.state.cards[library[-1]]
    card.known_to = list(host.seats)
    card.revealed_to = list(host.seats)
    host._log(
        actor,
        "library.reveal",
        f"{seat} revealed {card.ref} {card.printed_name}.",
        {"player": seat, "object": card.ref},
        importance=2,
        changed_objects=[card.object_id],
    )
    data = host._effective_card_data(card)
    types, _, _ = host._type_parts(
        str(data.get("type_line") or "")
    )
    if types.intersection(
        {
            "artifact",
            "battle",
            "creature",
            "enchantment",
            "land",
            "planeswalker",
        }
    ):
        host.move_card(
            card.object_id,
            "battlefield",
            controller=seat,
            reason=reason,
            semantic_events=True,
        )
    return card.ref


HANDLERS = {
    'prepare_graveyard_creature_aura': _apply_prepare_graveyard_creature_aura,
    'reanimate_attached_creature_aura': _apply_reanimate_attached_creature_aura,
    'attach': _apply_attach,
    'bestow_prepare': _apply_bestow_prepare,
    'bounce': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'destroy': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'destroy_selected': _apply_destroy_selected,
    'discard': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'exile': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'exile_all': _apply_exile_all,
    'exile_graveyard': _apply_exile_graveyard,
    'exile_opponent_graveyards': _apply_exile_opponent_graveyards,
    'mill': _apply_mill,
    'move': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'move_if_in_zone': _apply_move_if_in_zone,
    'pump_controlled_creatures': _apply_pump_controlled_creatures,
    REANIMATE_OPERATION: _apply_reanimate,
    'reveal_top_permanent': _apply_reveal_top_permanent,
    'sacrifice': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'shuffle_graveyard_bottom_random': _apply_shuffle_graveyard_bottom_random,
    'shuffle_into_library': _apply_shuffle_into_library,
    'modify_all_matching_permanents_until_end_of_turn': _apply_modify_all_matching_permanents_until_end_of_turn,
    'exchange_artifact_zones': _apply_exchange_artifact_zones,
}


def apply_effect(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    handler = HANDLERS.get(operation)
    if handler is None:
        raise GameRuleError(
            f"Unsupported owned effect {operation!r}"
        )
    return handler(
        host,
        effect,
        actor=actor,
        operation=operation,
        reason=reason,
    )
