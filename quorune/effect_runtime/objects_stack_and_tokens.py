from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ..counter_placement import (
    CounterPlacementError,
    place_counters_on_controlled_subtype,
    place_counters_on_refs,
)
from ..counter_removal import (
    commit_counter_removal_effect,
    CounterRemoval,
    CounterRemovalError,
    plan_counter_removal_effect,
)
from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ContinuousEffectStateError,
    create_resolution_continuous_effect,
    resolution_effect_source,
)
from ..errors import GameRuleError
from ..effect_contracts import effect_family_contract
from ..impulse_access import grant_temporary_cast_permission
from ..impulse_access_model import (
    ImpulseAccessDuration,
    TemporaryCastPermissionError,
    TemporaryCastPermissionGrant,
)
from ..permanent_transform import commit_transform_batch
from ..util import unique_preserving_order
from ..trigger_processing import schedule_delayed_trigger
from ..token_creation import TokenCreationError, create_token_batch


OPERATIONS = effect_family_contract("objects-stack-and-tokens.v1").operations
_REASON_FIELD = "reason"


def _commit_temporary_characteristic_effect(
    host: Any,
    effect: Mapping[str, Any],
    card: Any,
    *,
    layer: Layer,
    sublayer: str,
    operations: tuple[ContinuousOperation, ...],
) -> bool:
    """Return false only for historical annotation-backed checkpoints."""

    if host.state.continuous_effects is None:
        return False
    if not operations:
        return True
    try:
        created = create_resolution_continuous_effect(
            host,
            source=resolution_effect_source(
                host, effect, fallback_card=card
            ),
            targets=(card,),
            layer=layer,
            sublayer=sublayer,
            operations=operations,
        )
        if created is None:
            raise ContinuousEffectStateError(
                "Resolution continuous-effect commit returned no effect"
            )
    except ContinuousEffectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    return True


def _apply_delayed_trigger(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    source = effect.get("source")
    source_id = None
    if source:
        source_id = host._resolve_object(actor, str(source)).object_id
    return schedule_delayed_trigger(
        host,
        controller=str(effect.get("controller") or actor),
        label=str(effect["label"]),
        event_kind=str(effect.get("event") or "step.begin"),
        condition=dict(effect.get("condition") or {}),
        stack_template=dict(effect.get("stack") or {}),
        source_object_id=source_id,
        once=bool(effect.get("once", True)),
        expires_turn_sequence=effect.get("expires_turn_sequence"),
    ).ref



def _apply_create_token(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    copy_source = effect.get("copy_of")
    token_name = (
        str(effect["name"])
        if "name" in effect
        else ("" if copy_source else "Token")
    )
    return host.create_token(
        str(effect.get("controller") or actor),
        name=token_name,
        quantity=int(effect.get("quantity", 1)),
        tapped=bool(effect.get("tapped", False)),
        attacking=effect.get("attacking"),
        copy_of=copy_source,
        characteristics=dict(effect.get("characteristics") or {}),
        temporary_keywords=list(effect.get("temporary_keywords") or []),
        aura_target_ref=effect.get("aura_target_ref"),
        reason=reason,
        replacement_selections=tuple(
            effect.get("_replacement_selections") or ()
        ),
    )


def _apply_create_token_batch(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    try:
        return create_token_batch(
            host,
            str(effect.get("controller") or actor),
            tokens=effect.get("tokens"),
            reason=reason,
            replacement_selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
        )
    except TokenCreationError as exc:
        raise GameRuleError(str(exc)) from exc



def _apply_create_token_if_no_controlled_subtype(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    controller = str(effect.get("controller") or actor)
    subtype = str(effect.get("subtype") or "").casefold()
    if not subtype:
        raise GameRuleError(
            "Conditional token effect requires a subtype"
        )
    if any(
        host.state.cards[object_id].controller == controller
        and subtype
        in host._type_parts(
            str(
                host._effective_card_data(object_id).get(
                    "type_line"
                )
                or ""
            )
        )[1]
        for object_id in host.state.players[
            controller
        ].zones["battlefield"]
    ):
        return []
    return host.create_token(
        controller,
        name=str(effect.get("name") or subtype.title()),
        quantity=int(effect.get("quantity", 1)),
        tapped=bool(effect.get("tapped", False)),
        characteristics=dict(
            effect.get("characteristics") or {}
        ),
        reason=reason,
    )



def _apply_add_type_until_end_of_turn(
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
        zones={"battlefield"},
    )
    card_type = str(effect.get("type") or "").strip().title()
    if not card_type:
        raise GameRuleError("Temporary type effect requires a type")
    if not _commit_temporary_characteristic_effect(
        host,
        effect,
        card,
        layer=Layer.TYPE,
        sublayer="4",
        operations=(
            ContinuousOperation(
                "add_types", [card_type], field="card_types"
            ),
        ),
    ):
        until_end = card.annotations.setdefault("until_end_of_turn", {})
        until_end["add_types"] = unique_preserving_order(
            [
                *list(until_end.get("add_types") or []),
                card_type,
            ]
        )
    host._log(
        actor,
        "permanent.type",
        f"{card.ref} became a {card_type} in addition to its other types until end of turn.",
        {
            "object": card.ref,
            "type": card_type,
            _REASON_FIELD: reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_add_types_until_end_of_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    """Add one closed set of card types in a single layer-4 result."""

    del operation
    card = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"battlefield"},
    )
    raw_types = effect.get("types")
    if not isinstance(raw_types, (list, tuple)) or not raw_types:
        raise GameRuleError("Temporary add-types effect requires card types")
    if any(type(value) is not str or not value.strip() for value in raw_types):
        raise GameRuleError(
            "Temporary add-types values must be nonempty strings"
        )
    card_types = tuple(value.strip().title() for value in raw_types)
    if len(card_types) != len(set(card_types)):
        raise GameRuleError("Temporary add-types values must be distinct")
    parsed_types = host._type_parts(" ".join(card_types))[0]
    if parsed_types != {value.casefold() for value in card_types}:
        raise GameRuleError(
            "Temporary add-types values must use canonical card types"
        )
    if not _commit_temporary_characteristic_effect(
        host,
        effect,
        card,
        layer=Layer.TYPE,
        sublayer="4",
        operations=(
            ContinuousOperation(
                "add_types", card_types, field="card_types"
            ),
        ),
    ):
        raise GameRuleError(
            "Temporary add-types requires the continuous-effect journal"
        )
    label = " ".join(card_types)
    host._log(
        actor,
        "permanent.types",
        (
            f"{card.ref} became an {label} in addition to its other types "
            "until end of turn."
        ),
        {
            "object": card.ref,
            "types": list(card_types),
            _REASON_FIELD: reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_add_subtype_until_end_of_turn(
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
        zones={"battlefield"},
    )
    subtype = str(effect.get("subtype") or "").strip().title()
    if not subtype:
        raise GameRuleError(
            "Temporary subtype effect requires a subtype"
        )
    if not _commit_temporary_characteristic_effect(
        host,
        effect,
        card,
        layer=Layer.TYPE,
        sublayer="4",
        operations=(
            ContinuousOperation(
                "add_types", [subtype], field="subtypes"
            ),
        ),
    ):
        until_end = card.annotations.setdefault(
            "until_end_of_turn",
            {},
        )
        until_end["add_subtypes"] = unique_preserving_order(
            [
                *list(until_end.get("add_subtypes") or []),
                subtype,
            ]
        )
    host._log(
        actor,
        "permanent.subtype",
        (
            f"{card.ref} became a {subtype} in addition to "
            "its other types until end of turn."
        ),
        {
            "object": card.ref,
            "subtype": subtype,
            "reason": reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_copy_until_end_of_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    source = host._resolve_object(
        actor,
        str(effect.get("source") or effect.get("card")),
        zones={"battlefield"},
    )
    target = host._resolve_object(
        actor,
        str(effect.get("target")),
    )
    until_end = source.annotations.setdefault(
        "until_end_of_turn",
        {},
    )
    if "copy_overrides_previous" not in until_end:
        until_end["copy_overrides_previous"] = copy.deepcopy(
            source.annotations.get("copy_overrides")
        )
    characteristics = host._copyable_characteristics(target)
    if effect.get("except_nonlegendary"):
        type_line = str(
            characteristics.get("type_line") or ""
        )
        characteristics["type_line"] = re.sub(
            r"\bLegendary\s+",
            "",
            type_line,
            count=1,
            flags=re.IGNORECASE,
        )
    source.annotations["copy_overrides"] = characteristics
    host._log(
        actor,
        "permanent.copy",
        (
            f"{source.ref} became a copy of {target.ref} until "
            "end of turn."
        ),
        {
            "source": source.ref,
            "target": target.ref,
            "duration": "until_end_of_turn",
            "reason": reason,
        },
        importance=2,
        changed_objects=[source.object_id],
    )
    return source.ref



def _apply_change_control_until_end_of_turn(
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
        zones={"battlefield"},
    )
    new_controller = str(
        effect.get("controller") or actor
    )
    until_end = card.annotations.setdefault(
        "until_end_of_turn",
        {},
    )
    until_end.setdefault("control_previous", card.controller)
    host.change_control(
        card.object_id,
        new_controller,
        reason=reason,
    )
    return card.ref



def _apply_add_type(
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
        zones={"battlefield"},
    )
    card_type = str(effect.get("type") or "").strip().title()
    if not card_type:
        raise GameRuleError("Continuous type effect requires a type")
    card.annotations["continuous_add_types"] = (
        unique_preserving_order(
            [
                *list(
                    card.annotations.get(
                        "continuous_add_types", []
                    )
                ),
                card_type,
            ]
        )
    )
    host._log(
        actor,
        "permanent.type",
        (
            f"{card.ref} became a {card_type} in addition to its "
            "other types."
        ),
        {
            "object": card.ref,
            "type": card_type,
            "reason": reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_add_subtype(
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
        zones={"battlefield"},
    )
    subtype = str(effect.get("subtype") or "").strip().title()
    if not subtype:
        raise GameRuleError("Continuous subtype effect requires a subtype")
    card.annotations["continuous_add_subtypes"] = (
        unique_preserving_order(
            [
                *list(
                    card.annotations.get(
                        "continuous_add_subtypes", []
                    )
                ),
                subtype,
            ]
        )
    )
    host._log(
        actor,
        "permanent.subtype",
        (
            f"{card.ref} became a {subtype} in addition to its "
            "other types."
        ),
        {
            "object": card.ref,
            "subtype": subtype,
            "reason": reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_grant_keyword_until_end_of_turn(
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
        zones={"battlefield"},
    )
    keyword = str(effect["keyword"])
    if not _commit_temporary_characteristic_effect(
        host,
        effect,
        card,
        layer=Layer.ABILITY,
        sublayer="6",
        operations=(ContinuousOperation("add_ability", keyword),),
    ):
        card.temporary_keywords = unique_preserving_order(
            [*card.temporary_keywords, keyword]
        )
    host._log(
        actor,
        "permanent.keyword",
        f"{card.ref} gained {keyword} until end of turn.",
        {
            "object": card.ref,
            "keyword": keyword,
            "reason": reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_modify_stats_until_end_of_turn(
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
        zones={"battlefield"},
    )
    power = int(effect.get("power", 0))
    toughness = int(effect.get("toughness", 0))
    if not _commit_temporary_characteristic_effect(
        host,
        effect,
        card,
        layer=Layer.POWER_TOUGHNESS,
        sublayer="7c",
        operations=(
            (
                ContinuousOperation(
                    "modify_power_toughness", [power, toughness]
                ),
            )
            if power or toughness
            else ()
        ),
    ):
        until_end = card.annotations.setdefault(
            "until_end_of_turn",
            {},
        )
        until_end["power"] = int(until_end.get("power", 0)) + power
        until_end["toughness"] = (
            int(until_end.get("toughness", 0)) + toughness
        )
    host._log(
        actor,
        "permanent.stats",
        (
            f"{card.ref} got {power:+d}/{toughness:+d} until "
            "end of turn."
        ),
        {
            "object": card.ref,
            "power": power,
            "toughness": toughness,
            "reason": reason,
        },
        importance=2,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_grant_play_without_mana_cost(
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
        zones={"exile"},
    )
    if card.owner == actor:
        raise GameRuleError(
            "Dauthi Voidwalker can choose only a card an opponent owns"
        )
    if int(card.counters.get("void", 0)) <= 0:
        raise GameRuleError(
            "The chosen exiled card does not have a void counter"
        )
    card.annotations["temporary_play_permission"] = {
        "player": actor,
        "zone": "exile",
        "turn_sequence": host.state.turn_sequence,
        "without_mana_cost": True,
        "allow_land": True,
        "allow_spell": True,
        "source": str(effect.get("source") or ""),
    }
    host._log(
        actor,
        "permission.play",
        (
            f"{actor} may play {card.ref} from exile this turn "
            "without paying its mana cost."
        ),
        {
            "player": actor,
            "object": card.ref,
            "zone": "exile",
            "turn_sequence": host.state.turn_sequence,
            "without_mana_cost": True,
            "reason": reason,
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[actor],
    )
    return card.ref



def _apply_grant_cast_permission(
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
        zones={str(effect.get("zone") or "graveyard")},
        owned_only=bool(effect.get("owned_only", True)),
    )
    allowed_types = {
        str(value).casefold()
        for value in effect.get("types_any", [])
    }
    card_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(card).get("type_line")
            or ""
        )
    )
    if allowed_types and not allowed_types.intersection(
        card_types
    ):
        raise GameRuleError(
            "The selected card does not satisfy the cast permission"
        )
    duration = effect.get("duration")
    if duration not in {None, "until_used"}:
        raise GameRuleError("Cast-permission duration is unsupported")
    try:
        grant = TemporaryCastPermissionGrant(
            player=actor,
            duration=(
                ImpulseAccessDuration.UNTIL_USED
                if duration == "until_used"
                else ImpulseAccessDuration.END_OF_TURN
            ),
            not_before_turn_sequence=effect.get(
                "not_before_turn_sequence"
            ),
            without_mana_cost=bool(
                effect.get("without_mana_cost", False)
            ),
            source=str(effect.get("source") or ""),
        )
    except TemporaryCastPermissionError as exc:
        raise GameRuleError(str(exc)) from exc
    return grant_temporary_cast_permission(
        host,
        card=card,
        grant=grant,
        reason=reason,
    )



def _apply_counter(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    card = host._resolve_object(actor, str(effect["card"]), zones={"battlefield"})
    name = str(effect.get("counter") or "+1/+1")
    delta = int(effect.get("delta", 1))
    if delta > 0:
        try:
            results = place_counters_on_refs(
                host,
                actor=actor,
                object_refs=(card.ref,),
                counter_name=name,
                amount=delta,
                selections=tuple(
                    effect.get("_replacement_selections") or ()
                ),
                reason=reason,
                source_ref=str(effect.get("source") or "") or None,
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        return results[0].after
    try:
        result = commit_counter_removal_effect(
            host,
            plan_counter_removal_effect(
                host,
                CounterRemoval(
                    object_id=card.object_id,
                    counter_name=name,
                    amount=-delta,
                    expected_zone="battlefield",
                    expected_logical_object_id=card.logical_object_id,
                ),
            ),
        )
    except CounterRemovalError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(
        actor,
        "permanent.counter",
        f"{card.ref} {name} changed by {-result.removed}.",
        {
            "object": card.ref,
            "counter": result.counter_name,
            "requested_delta": delta,
            "delta": -result.removed,
            "before": result.before,
            "after": result.after,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    if (
        result.counter_name == "defense"
        and result.before > 0
        and result.after == 0
    ):
        host._queue_siege_defeated_trigger(card)
    return result.after



def _apply_counter_all_subtype(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    subtype = str(effect.get("subtype") or "").casefold()
    counter_name = str(effect.get("counter") or "+1/+1")
    amount = int(effect.get("amount", 1))
    try:
        results = place_counters_on_controlled_subtype(
            host,
            actor=actor,
            controller=seat,
            subtype=subtype,
            counter_name=counter_name,
            amount=amount,
            selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
            reason=reason,
            source_ref=str(effect.get("source") or "") or None,
        )
    except CounterPlacementError as exc:
        raise GameRuleError(str(exc)) from exc
    return [
        host.state.cards[result.object_id].ref
        for result in results
    ]



def _apply_look_top(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    viewer = str(effect.get("viewer") or actor)
    host._require_seat(seat, in_game=True)
    host._require_seat(viewer, in_game=True)
    try:
        count = int(effect.get("count", 1))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(
            "Library look count must be an integer"
        ) from exc
    if count < 0:
        raise GameRuleError(
            "Library look count cannot be negative"
        )
    library = host.state.players[seat].zones["library"]
    ids = (
        list(reversed(library[-count:]))
        if count
        else []
    )
    for oid in ids:
        card = host.state.cards[oid]
        card.known_to = sorted(set(card.known_to).union({viewer}))
    host._log(actor, "library.look", f"{viewer} looked at the top {len(ids)} card(s) of {seat}'s library.", {"player": seat, "count": len(ids)}, visibility=[viewer, "analyst"], importance=1, changed_objects=ids)
    return [host.state.cards[oid].ref for oid in ids]



def _apply_reorder_top(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    viewer = str(effect.get("viewer") or actor)
    host._require_seat(seat, in_game=True)
    host._require_seat(viewer, in_game=True)
    values = list(effect.get("cards") or [])  # top-first
    ids = [host._resolve_object(viewer, str(value), zones={"library"}).object_id for value in values]
    library = host.state.players[seat].zones["library"]
    if len(ids) != len(set(ids)):
        raise GameRuleError(
            "The same library card cannot be reordered twice"
        )
    current_top = (
        list(reversed(library[-len(ids):]))
        if ids
        else []
    )
    if (
        set(ids) != set(current_top)
        or any(
            viewer not in host.state.cards[oid].known_to
            for oid in ids
        )
    ):
        raise GameRuleError(
            "Can only reorder the exact known cards currently on top"
        )
    for oid in ids:
        library.remove(oid)
    # Internal library order stores top at the end.
    library.extend(reversed(ids))
    host._log(actor, "library.reorder", f"{viewer} reordered {len(ids)} known top cards.", {"count": len(ids)}, visibility=[viewer, "analyst"], importance=1, changed_objects=ids)
    return [host.state.cards[oid].ref for oid in ids]



def _apply_change_control(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    card = host._resolve_object(actor, str(effect["card"]), zones={"battlefield"})
    host.change_control(card.object_id, str(effect["controller"]), reason=reason)
    return card.ref



def _apply_note(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    host._log(actor, "rules.note", str(effect.get("text") or ""), {"reason": reason}, visibility=["arbiter", "analyst"], importance=0)
    return None


def _apply_transform(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    value = effect.get("card")
    if not value:
        return None
    expected = effect.get("expected_transform_count")
    if type(expected) is not int or expected < 0:
        raise GameRuleError(
            "Triggered transform requires its nonnegative source snapshot"
        )
    try:
        card = host._resolve_object(
            actor,
            str(value),
            zones={"battlefield"},
        )
    except GameRuleError:
        return None
    results = commit_transform_batch(
        host,
        (card,),
        reason=reason,
        day_night_instruction=False,
        expected_transform_counts={card.object_id: expected},
    )
    return results[0].card_ref if results else None


HANDLERS = {
    'add_subtype': _apply_add_subtype,
    'add_subtype_until_end_of_turn': _apply_add_subtype_until_end_of_turn,
    'add_type': _apply_add_type,
    'add_type_until_end_of_turn': _apply_add_type_until_end_of_turn,
    'add_types_until_end_of_turn': _apply_add_types_until_end_of_turn,
    'change_control': _apply_change_control,
    'change_control_until_end_of_turn': _apply_change_control_until_end_of_turn,
    'copy_until_end_of_turn': _apply_copy_until_end_of_turn,
    'counter': _apply_counter,
    'counter_all_subtype': _apply_counter_all_subtype,
    'create_token': _apply_create_token,
    'create_token_batch': _apply_create_token_batch,
    'create_token_if_no_controlled_subtype': _apply_create_token_if_no_controlled_subtype,
    'delayed_trigger': _apply_delayed_trigger,
    'grant_cast_permission': _apply_grant_cast_permission,
    'grant_keyword_until_end_of_turn': _apply_grant_keyword_until_end_of_turn,
    'grant_play_without_mana_cost': _apply_grant_play_without_mana_cost,
    'look_top': _apply_look_top,
    'modify_stats_until_end_of_turn': _apply_modify_stats_until_end_of_turn,
    'note': _apply_note,
    'reorder_top': _apply_reorder_top,
    'transform': _apply_transform,
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
