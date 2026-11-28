from __future__ import annotations

"""Resolve both numeric requests through the existing life and characteristic owners."""

from ..continuous_effect_model import ContinuousEffectDuration, ContinuousOperation, Layer
from ..continuous_effect_state import (
    ContinuousEffectStateError, ResolutionContinuousComponent, ResolutionEffectSource,
    create_resolution_continuous_effect_components,
)
from ..errors import GameRuleError
from ..life_state import LifeChange
from ..replacement.immutable import thaw_value
from ..rules.characteristic_life_exchange import (
    CHARACTERISTIC_LIFE_EXCHANGE_OPERATION, CharacteristicLifeExchangeSpec,
    characteristic_life_exchange_program_dependencies, plan_characteristic_life_exchange,
)
from ..semantic_runtime.life_prohibitions import collect_life_gain_permissions


def _resolve_exchange(host, effect, *, actor):
    allowed = {"op", "exchange", "source", "player"}
    if (
        set(effect) - {"_runtime_source", "_replacement_selections", "reason"} != allowed
        or effect["op"] != CHARACTERISTIC_LIFE_EXCHANGE_OPERATION
    ):
        raise GameRuleError("The exchange requires its complete bound instruction")
    try:
        spec = CharacteristicLifeExchangeSpec.from_mapping(effect["exchange"])
        source = ResolutionEffectSource.from_effect(effect)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    if (
        item is None or item.kind != "activated_ability" or item.controller != actor
        or item.context.get("currently_resolving") is not True or item.source_object_id != source.object_id
        or item.context.get("source_logical_object_id") != source.logical_object_id
    ):
        raise GameRuleError("The exchange needs its own resolving activation and source")
    program = host.semantics.get(item.semantic_key)
    if (
        program is None or not host.semantic_program_is_current_trusted(program)
        or thaw_value(program.effects) != [spec.to_instruction()]
        or not characteristic_life_exchange_program_dependencies(program)
    ):
        raise GameRuleError("The exchange needs its complete trusted activation")
    if spec.recipient == "controller":
        player = actor
    elif item.context.get("targets_revalidated") is True and len(item.targets) == 1:
        player = item.targets[0]
    else:
        raise GameRuleError("The exchange needs its revalidated opponent")
    if player != effect["player"]:
        raise GameRuleError("The exchange recipient contradicts its resolving activation")
    card = host.state.cards.get(source.object_id)
    if (
        card is None or card.zone != "battlefield" or card.phased_out
        or card.logical_object_id != source.logical_object_id or player not in host.active_seats
    ):
        return None
    if card.ref != effect["source"] or card.ref != source.card_ref:
        raise GameRuleError("The exchange source changed")
    types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    if "creature" not in types:
        return None
    return source, card, player, spec


def apply_characteristic_life_exchange(host, effect, *, actor, operation, reason):
    from .life_effects import commit_prepared_life_effect_changes, prepare_life_effect_changes

    resolved = _resolve_exchange(host, effect, actor=actor)
    if resolved is None:
        return None
    source, card, player, spec = resolved
    permissions = collect_life_gain_permissions(host)
    plan = plan_characteristic_life_exchange(
        life_total=host.state.players[player].life,
        effective_characteristic=host._numeric_stat(card.object_id, spec.characteristic),
        can_gain_life=permissions.allows_gain(player), can_lose_life=permissions.allows_loss(player),
    )
    if plan is None:
        return None
    prepared = prepare_life_effect_changes(
        host, (LifeChange(player, plan.life_delta),),
        effect=effect, actor=actor, reason=reason,
    )
    component = ResolutionContinuousComponent(
        Layer.POWER_TOUGHNESS, "7b",
        (ContinuousOperation("set_" + spec.characteristic, plan.new_base_characteristic),),
    )
    try:
        create_resolution_continuous_effect_components(
            host, source=source, targets=(card,), components=(component,),
            duration=ContinuousEffectDuration.ZONE_OBJECT,
        )
    except ContinuousEffectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    commit_prepared_life_effect_changes(host, prepared)
    host._log(actor, "permanent.stats", f"{card.ref} exchanged {spec.characteristic} with {player}'s life total.",
              {"object": card.ref, "player": player, "characteristic": spec.characteristic, "reason": reason},
              importance=2, changed_objects=[card.object_id], changed_players=[player])
    return card.ref
