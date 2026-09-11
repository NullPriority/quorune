from __future__ import annotations

from typing import Any, Mapping

from ..counter_placement import (
    CounterPlacementError,
    CounterPlacementRequest,
    place_counters,
)
from ..creature_power_damage import (
    CreaturePowerDamageError,
    resolve_creature_power_damage,
)
from ..damage import (
    DamageError,
    damage_proposal,
    resolve_damage_batch,
    source_snapshot,
)
from ..damage_source import DamageSourceSnapshot
from ..destruction import destroy_permanent_refs
from ..errors import GameRuleError
from ..effect_contracts import effect_family_contract
from ..semantic_runtime.intents import PlaceCountersIntent
from ..trigger_processing import schedule_delayed_trigger


OPERATIONS = effect_family_contract("damage-life-and-turns.v1").operations
_REASON_FIELD = "reason"


def _apply_creature_power_damage(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    if operation != "creature_power_damage":
        raise GameRuleError("Creature-power damage operation is unsupported")
    try:
        result = resolve_creature_power_damage(
            host,
            effect,
            actor=actor,
            reason=reason,
        )
    except CreaturePowerDamageError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(
        actor,
        "effect.creature_power_damage",
        f"Resolved {effect.get('kind')} creature-power damage.",
        {
            "kind": effect.get("kind"),
            "source": effect.get("source"),
            "target": effect.get("target"),
            "dealt_amounts": list(result),
        },
        importance=2,
    )
    return result


def _apply_damage(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    target = str(effect["target"])
    amount = int(effect.get("amount", 0))
    if amount < 0:
        raise GameRuleError("Damage cannot be negative")
    if amount == 0:
        return 0
    try:
        raw_source_snapshot = effect.get("source_snapshot")
        if raw_source_snapshot is not None and not isinstance(
            raw_source_snapshot, Mapping
        ):
            raise GameRuleError("Damage source LKI is malformed")
        pinned_source = (
            DamageSourceSnapshot.from_dict(dict(raw_source_snapshot))
            if isinstance(raw_source_snapshot, Mapping)
            else None
        )
        effect_source = (
            str(effect["source"])
            if effect.get("source") is not None
            else None
        )
        if pinned_source is not None and effect_source != pinned_source.ref:
            raise GameRuleError(
                "Damage source LKI does not match the represented source"
            )
        replacement_event_ids = list(
            effect.get("_replacement_event_ids") or ()
        )
        if replacement_event_ids and len(replacement_event_ids) != 1:
            raise GameRuleError(
                "Damage replacement event identity count is stale"
            )
        proposal = damage_proposal(
            host,
            proposal_id=(
                str(replacement_event_ids[0])
                if replacement_event_ids
                else str(
                    effect.get("damage_event_id")
                    or (
                        f"damage.effect:{host.state.revision}:"
                        f"{host.state.event_sequence + 1}:0"
                    )
                )
            ),
            actor=actor,
            source_ref=effect_source,
            target=target,
            amount=amount,
            combat=False,
            reason=reason,
            unpreventable=bool(effect.get("unpreventable", False)),
            source_override=pinned_source,
        )
        result = resolve_damage_batch(
            host,
            (proposal,),
            replacement_selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
        )
    except DamageError as exc:
        raise GameRuleError(str(exc)) from exc
    event = result.events[0]
    host._log(
        actor,
        (
            "effect.damage"
            if event.was_dealt
            else "effect.damage.prevented"
        ),
        (
            f"{event.target} took {event.dealt_amount} damage."
            if event.was_dealt
            else f"Damage to {event.target} was prevented."
        ),
        {
            "source": event.source,
            "target": event.target,
            "assigned_amount": event.assigned_amount,
            "amount": event.dealt_amount,
            "prevented_amount": event.prevented_amount,
            "reason": reason,
            "applied_effects": list(event.applied_effects),
            "damage_event": event.semantic_context(),
        },
        importance=2,
        changed_objects=result.changed_objects,
        changed_players=result.changed_players,
    )
    return event.dealt_amount



def _apply_damage_each_opponent(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    amount = int(effect.get("amount", 0))
    if amount < 0:
        raise GameRuleError("Damage cannot be negative")
    if amount == 0:
        return 0
    opponents = [
        seat
        for seat in host.active_seats
        if seat != actor
    ]
    try:
        replacement_event_ids = list(
            effect.get("_replacement_event_ids") or ()
        )
        if replacement_event_ids and len(replacement_event_ids) != len(
            opponents
        ):
            raise GameRuleError(
                "Damage replacement event identity count is stale"
            )
        proposals = tuple(
            damage_proposal(
                host,
                proposal_id=(
                    str(replacement_event_ids[index])
                    if replacement_event_ids
                    else (
                        f"damage.effect:{host.state.revision}:"
                        f"{host.state.event_sequence + 1}:{index}"
                    )
                ),
                actor=actor,
                source_ref=(
                    str(effect["source"])
                    if effect.get("source") is not None
                    else None
                ),
                target=opponent,
                amount=amount,
                combat=False,
                reason=reason,
                unpreventable=bool(
                    effect.get("unpreventable", False)
                ),
            )
            for index, opponent in enumerate(opponents)
        )
        result = resolve_damage_batch(
            host,
            proposals,
            replacement_selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
        )
    except DamageError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(
        actor,
        "effect.damage",
        f"Each opponent of {actor} was dealt damage.",
        {
            "opponents": opponents,
            "assigned_amount": amount,
            "dealt_amount": result.dealt_amount,
            "reason": reason,
            "damage_events": [
                event.semantic_context() for event in result.events
            ],
        },
        importance=2,
        changed_players=result.changed_players,
    )
    return result.dealt_amount


def _apply_energy(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    delta = effect.get("delta", 0)
    if type(delta) is not int or delta <= 0:
        raise GameRuleError(
            "Legacy energy effects require a positive exact counter amount"
        )
    if seat not in host.active_seats:
        raise GameRuleError("Energy counter recipient is not active")
    raw_selections = effect.get("_replacement_selections", ())
    if raw_selections is None:
        raw_selections = ()
    if not isinstance(raw_selections, (list, tuple)):
        raise GameRuleError(
            "Energy counter replacement selections must be an array"
        )
    try:
        results = place_counters(
            host,
            (
                CounterPlacementRequest(
                    subject_kind="player",
                    subject_id=seat,
                    counter_name="energy",
                    amount=delta,
                    placing_player=actor,
                    source_ref=(
                        str(effect["source"])
                        if effect.get("source") is not None
                        else None
                    ),
                ),
            ),
            selections=tuple(raw_selections),
            reason=reason,
        )
    except CounterPlacementError as exc:
        raise GameRuleError(str(exc)) from exc
    return results[0].after



def _apply_create_treasure(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    return host.create_token(
        str(effect.get("controller") or actor),
        name="Treasure",
        characteristics={
            "type_line": "Token Artifact — Treasure",
            "oracle_text": "{T}, Sacrifice this token: Add one mana of any color.",
            "activated_ability_profile": "tap_sac_any_color_mana_v1",
        },
        reason=reason,
    )



def _apply_create_modified_token_copy(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    controller = str(effect.get("controller") or actor)
    created = host.create_token(
        controller,
        name=str(effect.get("name") or ""),
        copy_of=str(effect["card"]),
        characteristics=dict(effect.get("characteristics") or {}),
        temporary_keywords=tuple(effect.get("temporary_keywords") or ()),
        reason=reason,
    )
    if not effect.get("sacrifice_on_controller_end_step"):
        return created
    for ref in created:
        token = host._resolve_object(
            actor, ref, zones={"battlefield"}, controlled_only=True
        )
        schedule_delayed_trigger(
            host,
            controller=controller,
            label=f"Sacrifice {token.ref}",
            event_kind="step.begin",
            condition={
                "phase": "ending",
                "step": "end_step",
                "player": "$controller",
            },
            stack_template={
                "label": f"Sacrifice {token.ref}",
                "semantic_key": "builtin:sacrifice-source",
            },
            source_object_id=token.object_id,
            once=True,
        )
    return created



def _apply_create_token_if_distinct_controlled_names(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    required_type = str(effect.get("required_type") or "land").casefold()
    names = {
        host.display_name(object_id)
        for object_id in host.state.players[actor].zones["battlefield"]
        if host.state.cards[object_id].controller == actor
        and host.card_record(object_id)
        and required_type
        in host._type_parts(
            str(host._effective_card_data(object_id).get("type_line") or "")
        )[0]
    }
    if len(names) < int(effect.get("minimum_distinct_names", 1)):
        return []
    token = dict(effect.get("token") or {})
    return host.create_token(
        str(effect.get("controller") or actor),
        name=str(token.get("name") or ""),
        quantity=int(token.get("quantity", 1)),
        characteristics=dict(token.get("characteristics") or {}),
        reason=reason,
    )



def _apply_create_token_copy_if_controlled_count(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    controller = str(effect.get("controller") or actor)
    required_type = str(effect.get("required_type") or "land").casefold()
    count = sum(
        1
        for object_id in host.state.players[
            controller
        ].zones["battlefield"]
        if host.state.cards[object_id].controller == controller
        and required_type
        in host._type_parts(
            str(
                host._effective_card_data(object_id).get(
                    "type_line"
                )
                or ""
            )
        )[0]
    )
    if count >= int(effect.get("threshold", 1)):
        return host.create_token(
            controller,
            name=str(effect.get("copy_name") or ""),
            copy_of=str(effect["copy_of"]),
            reason=reason,
        )
    fallback = dict(effect.get("fallback_token") or {})
    return host.create_token(
        controller,
        name=str(fallback.get("name") or ""),
        quantity=int(fallback.get("quantity", 1)),
        characteristics=dict(fallback.get("characteristics") or {}),
        reason=reason,
    )



def _apply_counter_or_destroy_blue(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    target = str(effect["target"])
    stack_item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == target
        ),
        None,
    )
    if stack_item is not None:
        if not stack_item.card_object_id:
            return None
        record = host.card_record(stack_item.card_object_id)
        if not record or "U" not in record.colors:
            return None
        return host._counter_stack_item(
            target,
            reason="Red/Pyroblast semantic",
            countered_by=actor,
        ).ref
    try:
        card = host._resolve_object(
            actor, target, zones={"battlefield"}
        )
    except GameRuleError:
        return None
    record = host.card_record(card)
    if not record or "U" not in record.colors:
        return None
    result = destroy_permanent_refs(
        host,
        (card.ref,),
        actor=actor,
        reason="Red/Pyroblast semantic",
    )
    return card.ref if result.destroyed_object_ids else None



def _apply_sacrifice_if_present(
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
    try:
        card = host._resolve_object(
            actor, str(value), zones={"battlefield"}
        )
    except GameRuleError:
        return None
    host.move_card(
        card.object_id,
        "graveyard",
        reason=reason,
        semantic_events=True,
    )
    return card.ref



def _apply_counter_stack(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    return host._counter_stack_item(
        str(effect["stack"]),
        destination=str(effect.get("destination") or "graveyard"),
        reason=reason,
        countered_by=actor,
    ).ref



def _apply_extra_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    return host.schedule_extra_turn(str(effect.get("player") or actor), source=str(effect.get("source") or reason)).turn_id



def _apply_control_next_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    target = str(effect.get("player") or "")
    if target not in host.active_seats:
        raise GameRuleError(
            "Turn-control effect requires an active player"
        )
    host.state.players[target].stats[
        "next_turn_controlled_by"
    ] = actor
    host._log(
        actor,
        "turn.control.scheduled",
        (
            f"{actor} will control {target} during that "
            "player's next turn."
        ),
        {
            "controller": actor,
            "player": target,
            "reason": reason,
        },
        importance=3,
        changed_players=[actor, target],
    )
    return target



def _apply_protection_from_everything_until_next_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    host._require_seat(seat, in_game=True)
    host.state.players[seat].stats[
        "protection_from_everything_until_next_turn"
    ] = True
    host._log(
        actor,
        "player.protection",
        (
            f"{seat} gained protection from everything until "
            "their next turn."
        ),
        {
            "player": seat,
            "duration": "until_next_turn",
            "reason": reason,
        },
        importance=2,
        changed_players=[seat],
    )
    return seat



def _apply_end_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    host._end_turn_now(actor=actor, reason=reason)
    return None



def _apply_create_emblem(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    controller = str(effect.get("controller") or actor)
    result = host.create_emblem(
        controller,
        abilities=tuple(str(value) for value in effect.get("abilities") or ()),
        display_label=str(effect.get("display_label") or "Emblem"),
        semantic_key=str(effect.get("semantic_key") or ""),
        reason=reason,
    )
    stats_counter = str(effect.get("stats_counter") or "")
    if stats_counter:
        player = host.state.players[controller]
        player.stats[stats_counter] = (
            int(player.stats.get(stats_counter, 0)) + 1
        )
    return result



def _apply_grant_ability_marker(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    if not bool(
        getattr(
            getattr(host, "semantics", None),
            "runtime_handler_compatibility_enabled",
            False,
        )
    ):
        raise GameRuleError(
            "String ability markers are historical Game Record v3 compatibility only"
        )
    source = host._resolve_object(
        actor,
        str(effect.get("source") or ""),
        zones={"battlefield"},
        controlled_only=True,
    )
    marker = str(effect.get("marker") or "").strip()
    if not marker:
        raise GameRuleError("Ability markers require a stable marker")
    source.annotations[marker] = True
    host._log(
        actor,
        "saga.ability.gained",
        f"{source.ref} gained an ability marker.",
        {
            "source": source.ref,
            "marker": marker,
            "reason": reason,
        },
        importance=2,
        changed_objects=[source.object_id],
        changed_players=[actor],
    )
    return marker


def _apply_grant_ability_fragment(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    from ..ability_fragments import (
        GrantedActivatedAbilitySpec,
        ability_fragment_from_dict,
        ability_fragment_to_dict,
        canonical_ability_fragments,
    )

    source = host._resolve_object(
        actor,
        str(effect.get("source") or ""),
        zones={"battlefield"},
        controlled_only=True,
    )
    raw_fragment = effect.get("fragment")
    if not isinstance(raw_fragment, Mapping):
        raise GameRuleError("Ability grants require one typed fragment")
    try:
        fragment = ability_fragment_from_dict(raw_fragment)
        if not isinstance(fragment, GrantedActivatedAbilitySpec):
            raise ValueError("only activated-ability fragments are supported")
        existing = canonical_ability_fragments(
            source.annotations.get("granted_ability_fragments", ())
        )
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    serialized = ability_fragment_to_dict(fragment)
    source.annotations["granted_ability_fragments"] = [
        *(ability_fragment_to_dict(value) for value in existing),
        serialized,
    ]
    host._log(
        actor,
        "ability.fragment.gained",
        f"{source.ref} gained a typed activated ability.",
        {
            "source": source.ref,
            "ability_id": fragment.ability_id,
            _REASON_FIELD: reason,
        },
        importance=2,
        changed_objects=[source.object_id],
        changed_players=[actor],
    )
    return serialized



def _apply_return_transformed(
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
        str(effect.get("card") or effect.get("source") or ""),
        zones={"exile"},
    )
    record = host.card_record(source)
    if record is None or len(record.faces) < 2:
        raise GameRuleError(
            "Return transformed requires a transforming card"
        )
    host.move_card(
        source.object_id,
        "battlefield",
        controller=source.owner,
        enter_face=str(record.faces[1].get("name") or ""),
        reason=reason,
        semantic_events=True,
    )
    return source.ref



def _apply_destroy_selected_and_reward_source(
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
        str(effect.get("source") or ""),
    )
    candidates: list[str] = []
    controlled_candidates: set[str] = set()
    for raw_ref in effect.get("cards") or []:
        if raw_ref is None:
            continue
        try:
            creature = host._resolve_object(
                actor,
                str(raw_ref),
                zones={"battlefield"},
            )
        except GameRuleError:
            continue
        types, _, _ = host._type_parts(
            str(
                host._effective_card_data(creature).get(
                    "type_line"
                )
                or ""
            )
        )
        if "creature" not in types:
            continue
        candidates.append(creature.ref)
        if creature.controller == actor:
            controlled_candidates.add(creature.object_id)
    result = destroy_permanent_refs(
        host,
        candidates,
        actor=actor,
        reason=reason,
    )
    destroyed_controlled = any(
        object_id in controlled_candidates
        for object_id in result.destroyed_object_ids
    )
    if (
        destroyed_controlled
        and source.zone == "battlefield"
        and source.controller == actor
    ):
        counter_name = str(effect.get("counter") or "+1/+1")
        counter_amount = int(effect.get("counter_amount", 0))
        host.place_counters_intent(
            PlaceCountersIntent(
                actor=actor,
                object_refs=(source.ref,),
                counter_name=counter_name,
                amount=counter_amount,
                reason=reason,
                source_ref=source.ref,
            )
        )
    return [
        host.state.cards[object_id].ref
        for object_id in result.destroyed_object_ids
    ]


HANDLERS = {
    'control_next_turn': _apply_control_next_turn,
    'counter_or_destroy_blue': _apply_counter_or_destroy_blue,
    'counter_stack': _apply_counter_stack,
    'create_emblem': _apply_create_emblem,
    'create_treasure': _apply_create_treasure,
    'create_modified_token_copy': _apply_create_modified_token_copy,
    'create_token_copy_if_controlled_count': _apply_create_token_copy_if_controlled_count,
    'create_token_if_distinct_controlled_names': _apply_create_token_if_distinct_controlled_names,
    'creature_power_damage': _apply_creature_power_damage,
    'damage': _apply_damage,
    'damage_each_opponent': _apply_damage_each_opponent,
    'destroy_selected_and_reward_source': _apply_destroy_selected_and_reward_source,
    'end_turn': _apply_end_turn,
    'energy': _apply_energy,
    'extra_turn': _apply_extra_turn,
    'grant_ability_marker': _apply_grant_ability_marker,
    'grant_ability_fragment': _apply_grant_ability_fragment,
    'protection_from_everything_until_next_turn': _apply_protection_from_everything_until_next_turn,
    'return_transformed': _apply_return_transformed,
    'sacrifice_if_present': _apply_sacrifice_if_present,
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
