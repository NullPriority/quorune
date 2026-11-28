from __future__ import annotations

"""Validate the shared authoritative APNAP instruction boundary."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..library_order_model import HAND_TOP_APNAP_RESULT, is_affected_hand_top_choice
from ..linked_exile_return_model import LINKED_FADING_HAND_BINDING, validate_linked_ability_binding
from ..apnap_object_payment import (
    DECLINED_LIFE_LOSS, ORDER_GRAVEYARD_ARRIVALS, OTHER_PLAYER_SACRIFICE_SCOPE,
    apnap_object_payment_dependencies,
)
from ..object_predicate import ObjectQuerySpec
from ..power_sacrifice_model import (
    ATTACK_SACRIFICE_STAT_FIELD, CONTROLLER_SACRIFICE_LIFE_FIELD, ENTRY_SACRIFICE_POWER_DRAW_FIELD,
    ENTRY_SACRIFICE_POWER_TREASURE_FIELD, validate_entry_sacrifice_power_treasure,
    GREATEST_POWER_SACRIFICE_FIELD, GREATEST_POWER_SACRIFICE_INCARNATIONS,
    GREATEST_POWER_SACRIFICE_LIFE_RESULT, GreatestPowerSacrificeLifeResult, GreatestPowerSacrificeSpec,
    validate_attack_sacrifice_stat_effect, validate_controller_sacrifice_toughness_life,
    validate_entry_sacrifice_power_life_draw,
)
from ..turn_history import current_turn_history_events
from ..rules.counted_sacrifice_tokens import COUNTED_SACRIFICE_TOKEN_FIELD, counted_sacrifice_token_parts
from ..rules.committed_sacrifice_damage import capture_committed_sacrifice_damage_target
from ..rules.any_number_sacrifice import (
    ANY_NUMBER_SACRIFICE_FIELD, any_number_sacrifice_count, any_number_sacrifice_parts, any_number_self_entry,
    is_sacrifice_search_chapter,
)
from ..rules.named_searches import SACRIFICE_BOUND_CREATURE_RECIPE_FIELD
from ..rules.target_sacrifice_toughness import TARGET_SACRIFICE_TOUGHNESS_FIELD, validate_target_sacrifice_toughness
from ..player_maximum import current_players_at_maximum
from ..player_maximum_model import LAND_COUNT_METRIC, uses_land_count_maximizers
from ..rules.shared_type_sacrifice import SHARED_TYPE_SACRIFICE
from .fractional_counts import apnap_required_count, validate_apnap_count
from .shared_type_sacrifice import validate_shared_sacrifice_occurrence


def bind_most_lands_sacrifice_players(host, effect, continuation):
    if not uses_land_count_maximizers(effect.get("players")):
        return effect

    from ..rules.affected_player_sacrifice_capability_shapes import most_lands_attack_sacrifice_effect_is_closed

    stack_ref = continuation.get("stack_ref")
    item = next((candidate for candidate in host.state.stack if candidate.ref == stack_ref), None)
    if (
        item is None or item.context.get("event") != "creature.attacks" or item.targets
        or not most_lands_attack_sacrifice_effect_is_closed(effect, actor=item.controller)
    ):
        raise GameRuleError("Most-lands sacrifice requires its exact sealed combat choice")
    try:
        players = current_players_at_maximum(
            host.state, LAND_COUNT_METRIC, public_object_resolver=host._public_object_query_result,
        )
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return {**effect, "players": list(players)}


def greatest_power_sacrifice_players(host, effect, continuation):
    if GREATEST_POWER_SACRIFICE_FIELD not in effect:
        return None
    stack_ref = continuation.get("stack_ref")
    item = next((candidate for candidate in host.state.stack if candidate.ref == stack_ref), None)
    if (item is None or item.controller not in host.active_seats
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Greatest-power sacrifice requires its current resolving source")
    try:
        spec = GreatestPowerSacrificeSpec.from_effect(effect, actor=item.controller)
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    kinds = {"activated_ability"} if spec.lost_life_only else {"spell", "spell_copy"}
    if item.kind not in kinds and not _is_greatest_power_loyalty_result(host, item, spec):
        raise GameRuleError("Greatest-power sacrifice has the wrong resolving object kind")
    if spec.opponent_only:
        from ..rules.gift_graveyard_spells import require_resolving_greatest_power_opponent

        try:
            require_resolving_greatest_power_opponent(host, item, spec.target_player)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return [spec.target_player]
    if spec.target_player is not None:
        if (tuple(item.targets) != (spec.target_player,) or spec.target_player not in host.active_seats
                or item.context.get("targets_revalidated") is not True):
            raise GameRuleError("Greatest-power sacrifice lost its revalidated affected player")
        return [spec.target_player]
    if item.targets:
        raise GameRuleError("Greatest-power sacrifice requires its current resolving source")
    opponents = [seat for seat in host.apnap_order() if seat != item.controller]
    if not spec.lost_life_only:
        return opponents
    history = host.state.turn_history
    turn = host.state.turn_sequence
    if history is None or history.schema_version != 1 or history.turn_sequence != turn:
        raise GameRuleError("Current opponent life-loss facts are unavailable")
    lost = {
        event.target for event in current_turn_history_events(history, turn_sequence=turn, kind="player_lost_life")
        if event.amount > 0
    }
    return [seat for seat in opponents if seat in lost]


def _is_greatest_power_loyalty_result(host, item, spec):
    if item.kind != "activated_ability" or spec.mana_value or spec.target_player is not None:
        return False
    from ..rules.affected_player_sacrifice_capability_shapes import greatest_power_sacrifice_runtime_dependencies

    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        return False
    try:
        return (bool(greatest_power_sacrifice_runtime_dependencies(program)) and len(program.effects) == 1
                and GreatestPowerSacrificeSpec.from_effect(program.effects[0]) == spec)
    except (TypeError, ValueError, KeyError):
        return False


def _target_sacrifice_toughness_player(host, effect, continuation):
    item = next((item for item in host.state.stack if item.ref == continuation.get("stack_ref")), None)
    target = effect.get("target")
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller not in host.active_seats
            or type(target) is not str or target not in host.active_seats or tuple(item.targets) != (target,)
            or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True):
        raise GameRuleError("Sacrificed toughness requires the original legal player target")
    try:
        validate_target_sacrifice_toughness(effect, actor=item.controller, target=target)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return target


def _controller_sacrifice_life_player(host, effect, continuation):
    item = next((item for item in host.state.stack if item.ref == continuation.get("stack_ref")), None)
    if (
        item is None or item.kind != "triggered_ability" or item.targets
        or item.controller not in host.active_seats or item.context.get("currently_resolving") is not True
        or (item.context.get("event"), item.context.get("step"), item.context.get("player"))
        != ("step.begin", "upkeep", item.controller)
    ):
        raise GameRuleError("Controller sacrifice life requires its original resolving upkeep trigger")
    try:
        validate_controller_sacrifice_toughness_life(effect, actor=item.controller)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return item.controller


def _source_sacrifice_stat_player(host, effect, continuation):
    from ..semantic_runtime.values import resolve_semantic_value

    attacking = ATTACK_SACRIFICE_STAT_FIELD in effect
    event = "creature.attacks" if attacking else "permanent.enter"
    validate_effect = validate_entry_sacrifice_power_life_draw
    if attacking:
        validate_effect = validate_attack_sacrifice_stat_effect
    elif ENTRY_SACRIFICE_POWER_TREASURE_FIELD in effect:
        validate_effect = validate_entry_sacrifice_power_treasure
    item = next((item for item in host.state.stack if item.ref == continuation.get("stack_ref")), None)
    if (
        item is None or item.kind != "triggered_ability" or item.targets
        or item.controller not in host.active_seats or item.context.get("currently_resolving") is not True
        or item.context.get("event") != event
        or not item.source_object_id or not item.context.get("source_logical_object_id")
    ):
        raise GameRuleError("Creature sacrifice stats require the original resolving source occurrence")
    try:
        excluded = resolve_semantic_value(host, "$source.zone_object", item)
        validate_effect(effect, actor=item.controller, excluded=excluded)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return item.controller


def sacrifice_can_decline(effect):
    marker = effect.get(ENTRY_SACRIFICE_POWER_DRAW_FIELD)
    return (
        SHARED_TYPE_SACRIFICE in effect or ENTRY_SACRIFICE_POWER_TREASURE_FIELD in effect
        or ATTACK_SACRIFICE_STAT_FIELD in effect
        or (isinstance(marker, Mapping) and marker.get("optional") is True)
    )


def revalidate_sacrifice_incarnations(host, state, player_order):
    effect = state["effect"]
    if SHARED_TYPE_SACRIFICE in effect:
        paid = validate_shared_sacrifice_occurrence(host, effect, state["resume"])
        actor = effect["actor"]
        players = [actor] if paid is None else [seat for seat in host.apnap_order() if seat != actor]
    elif ORDER_GRAVEYARD_ARRIVALS in effect:
        validate_apnap_choice_effect(host, effect, state["resume"])
        players = host.apnap_order()
    elif any(field in effect for field in (ATTACK_SACRIFICE_STAT_FIELD, ENTRY_SACRIFICE_POWER_DRAW_FIELD,
                                          ENTRY_SACRIFICE_POWER_TREASURE_FIELD)):
        players = [_source_sacrifice_stat_player(host, effect, state["resume"])]
    elif CONTROLLER_SACRIFICE_LIFE_FIELD in effect:
        players = [_controller_sacrifice_life_player(host, effect, state["resume"])]
    elif TARGET_SACRIFICE_TOUGHNESS_FIELD in effect:
        players = [_target_sacrifice_toughness_player(host, effect, state["resume"])]
    elif ANY_NUMBER_SACRIFICE_FIELD in effect:
        players = [_require_any_number_sacrifice(host, effect, state["resume"])]
    elif COUNTED_SACRIFICE_TOKEN_FIELD in effect:
        controller = _require_counted_sacrifice_tokens(host, effect, state["resume"])
        players = [seat for seat in host.apnap_order() if seat != controller]
    elif effect.get("players") == OTHER_PLAYER_SACRIFICE_SCOPE:
        controller = _require_other_player_sacrifice(host, effect, state["resume"])
        players = [seat for seat in host.apnap_order() if seat != controller]
    else:
        players = greatest_power_sacrifice_players(host, effect, state["resume"])
    if players is None:
        return
    selected = state["selected"]
    if players != player_order or not isinstance(selected, Mapping) or set(selected) != set(players):
        raise GameRuleError("Creature sacrifice has incomplete affected-player choices")
    incarnations = state.get(GREATEST_POWER_SACRIFICE_INCARNATIONS, {})
    if not isinstance(incarnations, Mapping):
        raise GameRuleError("Creature sacrifice incarnation evidence is malformed")
    for seat in players:
        refs = selected[seat]
        options = host._choice_options(seat, effect)
        required = (apnap_required_count(state, seat, options) if ORDER_GRAVEYARD_ARRIVALS in effect
                    else min(1, len(options)))
        cardinalities = (0, required) if sacrifice_can_decline(effect) else (required,)
        if (not isinstance(refs, (list, tuple))
                or not (len(refs) <= len(options) if any_number_sacrifice_count(effect.get("count"))
                        else len(refs) in cardinalities)
                or any(type(ref) is not str or ref not in options for ref in refs)):
            raise GameRuleError("A creature sacrifice choice is stale or ineligible")
        for ref in refs:
            card = host._resolve_object(seat, ref, zones={"battlefield"})
            if card.logical_object_id != incarnations.get(ref):
                raise GameRuleError("A creature sacrifice incarnation changed before commit")


def sacrifice_life_commit_fields(effect, state, *, actor, selected_refs):
    if not selected_refs:
        return {}
    try:
        sacrificing_player = None
        draw_after_life = ENTRY_SACRIFICE_POWER_DRAW_FIELD in effect
        draw_then_lose_life = ATTACK_SACRIFICE_STAT_FIELD in effect
        create_treasure = ENTRY_SACRIFICE_POWER_TREASURE_FIELD in effect
        if create_treasure:
            validate_entry_sacrifice_power_treasure(effect, actor=actor, excluded=effect.get("exclude_ref"))
            player, characteristic = actor, "power"
        elif draw_then_lose_life:
            validate_attack_sacrifice_stat_effect(effect, actor=actor, excluded=effect.get("exclude_ref"))
            player, characteristic = actor, "power"
        elif draw_after_life:
            validate_entry_sacrifice_power_life_draw(effect, actor=actor, excluded=effect.get("exclude_ref"))
            player, characteristic = actor, "power"
        elif CONTROLLER_SACRIFICE_LIFE_FIELD in effect:
            validate_controller_sacrifice_toughness_life(effect, actor=actor)
            player, characteristic = actor, "toughness"
        elif TARGET_SACRIFICE_TOUGHNESS_FIELD in effect:
            sacrificing_player = effect["target"]
            _relation, recipient = validate_target_sacrifice_toughness(effect, actor=actor, target=sacrificing_player)
            player = sacrificing_player if recipient == "target" else actor
            characteristic = "toughness"
        elif GREATEST_POWER_SACRIFICE_FIELD in effect:
            spec = GreatestPowerSacrificeSpec.from_effect(effect, actor=actor)
            if spec.target_player is None or spec.opponent_only:
                return {}
            player, characteristic = spec.target_player, "power"
        else:
            return {}
        ref, = selected_refs
        result = GreatestPowerSacrificeLifeResult(
            player, state[GREATEST_POWER_SACRIFICE_INCARNATIONS][ref], characteristic,
            draw_after_life=draw_after_life, draw_then_lose_life=draw_then_lose_life,
            sacrificing_player=sacrificing_player, create_treasure=create_treasure,
        )
        return {GREATEST_POWER_SACRIFICE_LIFE_RESULT: result.to_dict()}
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def is_typed_hand_exile(effect):
    return (
        effect.get("zone") == "hand" and effect.get("then") == "exile"
        and effect.get("predicate") is not None
    )


def _require_counted_sacrifice_tokens(host, effect, continuation):
    item = next((item for item in host.state.stack if item.ref == continuation.get("stack_ref")), None)
    if (item is None or item.targets or item.controller not in host.active_seats
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Counted sacrifice tokens require the original resolving controller")
    try:
        players, _token = counted_sacrifice_token_parts(effect, actor=item.controller)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if players == OTHER_PLAYER_SACRIFICE_SCOPE:
        if item.kind not in {"spell", "spell_copy"}:
            raise GameRuleError("Other-player counted tokens require the original resolving spell")
    else:
        identity = item.context.get("card_object_identity")
        if (item.kind != "triggered_ability" or item.context.get("event") != "permanent.enter"
                or type(identity) is not str or identity.rpartition("@")[0] != item.source_object_id):
            raise GameRuleError("Opponent counted tokens require the original self entry")
    return item.controller


def _require_other_player_sacrifice(host, effect, continuation):
    from ..apnap_object_payment import validate_other_player_sacrifice

    stack_ref = continuation.get("stack_ref")
    item = next((item for item in host.state.stack if item.ref == stack_ref), None)
    if (item is None or item.kind not in {"activated_ability", "triggered_ability"} or item.targets
            or item.controller not in host.active_seats or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Other-player sacrifice lost its untargeted resolving ability")
    try:
        validate_other_player_sacrifice(effect, actor=item.controller)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return item.controller


def _require_any_number_sacrifice(host, effect, continuation):
    from ..semantic_runtime.values import resolve_semantic_value

    item = next((item for item in host.state.stack if item.ref == continuation.get("stack_ref")), None)
    if (item is None or item.controller not in host.active_seats
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Any-number sacrifice requires its original resolving controller")
    try:
        marker = effect.get(ANY_NUMBER_SACRIFICE_FIELD)
        excludes_source = isinstance(marker, Mapping) and marker.get("domain") == "other_permanents"
        excluded = resolve_semantic_value(host, "$source.zone_object", item) if excludes_source else None
        domain, followup = any_number_sacrifice_parts(effect, actor=item.controller, excluded=excluded)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if followup["op"] == "damage":
        capture_committed_sacrifice_damage_target(host, item)
    elif item.targets:
        raise GameRuleError("This any-number sacrifice result has no target")
    if any_number_self_entry(domain, followup):
        identity = item.context.get("card_object_identity")
        if (item.kind != "triggered_ability" or item.context.get("event") != "permanent.enter"
                or type(identity) is not str or identity.rpartition("@")[0] != item.source_object_id):
            raise GameRuleError("Any-number sacrifice lost the original self entry")
    elif SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in followup:
        if item.kind != "triggered_ability" or not is_sacrifice_search_chapter(item.context.get("event")):
            raise GameRuleError("The sacrifice search lost its original resolving chapter")
    elif item.kind not in {"spell", "spell_copy"}:
        raise GameRuleError("Any-number sacrifice lost its original resolving spell")
    return item.controller


def _validate_fixed_group_sacrifice(host, effect, continuation):
    if ANY_NUMBER_SACRIFICE_FIELD in effect:
        _require_any_number_sacrifice(host, effect, continuation)
        return True
    if COUNTED_SACRIFICE_TOKEN_FIELD in effect:
        _require_counted_sacrifice_tokens(host, effect, continuation)
        return True
    if effect.get("players") == OTHER_PLAYER_SACRIFICE_SCOPE:
        _require_other_player_sacrifice(host, effect, continuation)
        return True
    return False


def caster_discard_player_for_choice(host, effect, continuation):
    from ..rules.opponent_caster_discard_payment import original_caster_discard_payer

    ref = continuation.get("stack_ref")
    item = next((candidate for candidate in host.state.stack if candidate.ref == ref), None)
    try:
        return original_caster_discard_payer(item, effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def validate_apnap_choice_effect(host, effect, continuation):
    from ..rules.ordered_opponent_creature_sacrifice import ordered_opponent_sacrifice_commit_fields, uses_ordered_opponent_sacrifice

    if uses_ordered_opponent_sacrifice(effect):
        item = next((entry for entry in host.state.stack if entry.ref == continuation.get("stack_ref")), None)
        ordered_opponent_sacrifice_commit_fields(host, item, effect)
        return
    from ..rules.apnap_counter_attack_truce import FIELD, validate_counter_attack_truce_choice
    if FIELD in effect:
        validate_counter_attack_truce_choice(host, effect, continuation)
        return

    from ..rules.opponent_caster_discard_payment import uses_caster_discard_payment
    from ..rules.sacrifice_discard_fallback import uses_sacrifice_discard_fallback
    from ..rules.selected_discard_draw import SELECTED_DISCARD_DRAW_FIELD, validate_resolving_selected_discard_choice

    if SELECTED_DISCARD_DRAW_FIELD in effect:
        item = next((entry for entry in host.state.stack if entry.ref == continuation.get("stack_ref")), None)
        try:
            validate_resolving_selected_discard_choice(host, item, effect)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if uses_sacrifice_discard_fallback(effect):
        _validate_sacrifice_discard_fallback_choice(host, effect, continuation)
        return

    if uses_caster_discard_payment(effect):
        caster_discard_player_for_choice(host, effect, continuation)
    else:
        _validate_apnap_choice_contract(host, effect, continuation)


def _validate_sacrifice_discard_fallback_choice(host, effect, continuation):
    from ..rules.sacrifice_discard_fallback import validate_sacrifice_discard_fallback

    item = next((candidate for candidate in host.state.stack
                 if candidate.ref == continuation.get("stack_ref")), None)
    if (item is None or item.kind != "triggered_ability" or item.targets or item.controller not in host.active_seats
            or item.context.get("currently_resolving") is not True or item.context.get("event") != "permanent.enter"):
        raise GameRuleError("Sacrifice and discard require the original resolving entry trigger")
    try:
        validate_sacrifice_discard_fallback(effect, actor=item.controller)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def _validate_apnap_choice_contract(host, effect, continuation):
    from ..rules.keep_three_sacrifice import KEEP_THREE_SACRIFICE_FIELD, keep_three_sacrifice_scope
    if 'scheduled_permanent_return_offered' in continuation:
        from ..drawing.model import DrawError
        from ..drawing.scheduled_permanent_return import require_scheduled_permanent_return

        try:
            require_scheduled_permanent_return(host, effect, continuation)
        except DrawError as exc:
            raise GameRuleError(str(exc)) from exc
        return
    validate_apnap_count(effect)
    if SHARED_TYPE_SACRIFICE in effect:
        validate_shared_sacrifice_occurrence(host, effect, continuation)
        return
    if ORDER_GRAVEYARD_ARRIVALS in effect:
        from ..rules.affected_player_sacrifice_capability_shapes import ordered_creature_sacrifice_effect_is_closed

        item = next((row for row in host.state.stack if row.ref == continuation.get("stack_ref")), None)
        if (item is None or item.targets or item.controller not in host.active_seats
                or item.context.get("currently_resolving") is not True
                or not ordered_creature_sacrifice_effect_is_closed(effect, actor=item.controller)):
            raise GameRuleError("Ordered creature sacrifice requires its original resolving instruction")
        return
    if _validate_fixed_group_sacrifice(host, effect, continuation):
        return
    if LINKED_FADING_HAND_BINDING in effect and not is_typed_hand_exile(effect):
        raise GameRuleError("A linked hand choice requires its typed hand exile")
    from ..rules.counter_marked_keep import KEEP_COUNTER_FIELD, require_counter_keep_effect

    if KEEP_COUNTER_FIELD in effect:
        ref = continuation.get("stack_ref")
        item = next((item for item in host.state.stack if item.ref == ref), None)
        if (item is None or item.kind not in {"spell", "spell_copy"} or item.targets
                or item.controller not in host.active_seats or item.context.get("currently_resolving") is not True):
            raise GameRuleError("Counter-marked retention requires its original resolving spell")
        try:
            require_counter_keep_effect(effect, actor=item.controller)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if KEEP_THREE_SACRIFICE_FIELD in effect:
        ref = continuation.get("stack_ref")
        item = next((item for item in host.state.stack if item.ref == ref), None)
        if (item is None or item.targets or item.controller not in host.active_seats
                or item.context.get("currently_resolving") is not True):
            raise GameRuleError("Keep-three sacrifice requires its original resolving object")
        try:
            scope = keep_three_sacrifice_scope(effect, actor=item.controller)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        if (scope == "land" and (item.kind != "triggered_ability" or item.context.get("event") != "permanent.enter")
                or scope == "permanent" and item.kind not in {"spell", "spell_copy"}):
            raise GameRuleError("Keep-three sacrifice requires its admitted resolution kind")
        return
    if GREATEST_POWER_SACRIFICE_FIELD in effect:
        greatest_power_sacrifice_players(host, effect, continuation)
        return
    if CONTROLLER_SACRIFICE_LIFE_FIELD in effect:
        _controller_sacrifice_life_player(host, effect, continuation)
        return
    if TARGET_SACRIFICE_TOUGHNESS_FIELD in effect:
        _target_sacrifice_toughness_player(host, effect, continuation)
        return
    if any(field in effect for field in (ATTACK_SACRIFICE_STAT_FIELD, ENTRY_SACRIFICE_POWER_DRAW_FIELD,
                                         ENTRY_SACRIFICE_POWER_TREASURE_FIELD)):
        _source_sacrifice_stat_player(host, effect, continuation)
        return
    hand_top = is_affected_hand_top_choice(effect)
    if hand_top and effect.get("predicate") is None:
        raise GameRuleError("Hand ordering requires a private own-hand predicate")
    try:
        optional_payment = bool(apnap_object_payment_dependencies(effect))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if effect.get("predicate") is not None:
        _validate_apnap_predicate_choice(
            host, effect, continuation, hand_top=hand_top, optional_payment=optional_payment,
        )


def _validate_apnap_predicate_choice(host, effect, continuation, *, hand_top, optional_payment):
    private_hand_choice = (
        effect.get("zone") == "hand"
        and effect.get("then") in {"discard", "exile", HAND_TOP_APNAP_RESULT}
    )
    allowed_fields = {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "prompt",
        *(("exclude_ref",) if "exclude_ref" in effect else ()),
        *(("require_full_count", "fallback_effects") if effect.get("require_full_count") is True else ()),
        *(("hidden",) if private_hand_choice else ()),
        *((DECLINED_LIFE_LOSS,) if optional_payment else ()),
        *(('target',) if "target" in effect else ()),
        *((LINKED_FADING_HAND_BINDING,) if LINKED_FADING_HAND_BINDING in effect else ()),
    }
    stack_ref = str(continuation.get("stack_ref") or "")
    item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == stack_ref
        ),
        None,
    )
    from ..rules.event_player_choice_shapes import attacked_defender_exile_effect_is_closed, is_typed_defender_exile

    if is_typed_defender_exile(effect):
        defender = item.context.get("defending_player") if item is not None else None
        if (
            item is None or item.context.get("event") != "creature.attacks"
            or type(defender) is not str or defender not in host.active_seats
            or not attacked_defender_exile_effect_is_closed(effect, actor=item.controller, player=defender)
        ):
            raise GameRuleError("Defender exile requires its exact sealed combat choice")
        return
    if (
        set(effect) != allowed_fields
        or effect.get("op") != "choose_cards_apnap"
        or (
            effect.get("zone"),
            effect.get("then"),
            effect.get("hidden", False),
        )
        not in {
            ("battlefield", "sacrifice", False),
            ("battlefield", "return_owner_hand", False),
            ("hand", "discard", True),
            ("hand", "exile", True),
            ("hand", HAND_TOP_APNAP_RESULT, True),
        }
        or type(effect.get("actor")) is not str
        or effect.get("actor") not in host.active_seats
        or item is None
        or effect.get("actor") != item.controller
        or (
            "target" in effect
            and effect.get("players") != [effect.get("target")]
        )
        or type(effect.get("prompt")) is not str
        or not str(effect.get("prompt")).strip()
        or (
            "exclude_ref" in effect
            and (type(effect.get("exclude_ref")) is not str or not effect.get("exclude_ref"))
        )
        or (
            effect.get("require_full_count") is True
            and (
                effect.get("then") != "return_owner_hand"
                or not isinstance(effect.get("fallback_effects"), list)
                or not effect.get("fallback_effects")
            )
        )
    ):
        raise GameRuleError("Typed APNAP choice effect is malformed")
    if (is_typed_hand_exile(effect) or hand_top) and (
        type(effect.get("count")) is not int or effect["count"] not in {1, 2, 3}
        or effect["predicate"] != ObjectQuerySpec(zones=("hand",)).to_dict()
        or "exclude_ref" in effect or "require_full_count" in effect
        or ("target" not in effect and effect.get("players") not in ("all", "opponents"))
    ):
        if hand_top:
            raise GameRuleError("Hand ordering requires a fixed private own-hand choice")
        raise GameRuleError("Typed hand exile requires a fixed private own-hand choice")
    if hand_top and "target" in effect and effect["target"] not in item.targets:
        raise GameRuleError("The ordered hand choice lost its announced player target")
    if LINKED_FADING_HAND_BINDING in effect:
        try:
            validate_linked_ability_binding(effect[LINKED_FADING_HAND_BINDING])
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        target = effect.get("target")
        if (effect.get("count") != 1 or target == item.controller or target not in host.active_seats
                or tuple(item.targets) != (target,) or item.kind != "activated_ability"
                or item.context.get("currently_resolving") is not True
                or item.context.get("targets_revalidated") is not True):
            raise GameRuleError("Linked hand exile requires the activated ability's revalidated opponent")


def revalidate_ordered_hand_choices(host, state, player_order):
    selected = state["selected"]
    incarnations = state.get("hand_top_incarnations", {})
    effect = state["effect"]
    expected = host.apnap_order()
    if "target" in effect:
        expected = [effect["target"]]
    elif effect["players"] == "opponents":
        expected = [seat for seat in expected if seat != effect["actor"]]
    if player_order != expected or set(selected) != set(expected):
        raise GameRuleError("Ordered hand choices are incomplete")
    for seat in player_order:
        cards = {
            card.ref: card.logical_object_id
            for object_id in host.state.players[seat].zones["hand"]
            if (card := host.state.cards[object_id]).owner == seat
        }
        if any(ref not in incarnations or cards.get(ref) != incarnations[ref] for ref in selected[seat]):
            raise GameRuleError("A selected hand card changed before simultaneous movement")
