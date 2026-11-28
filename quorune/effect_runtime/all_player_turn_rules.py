"""Commit a global turn rule through the canonical continuous-effect journal."""

from ..all_player_turn_rule_model import (
    AllPlayerTurnRule,
    ResolvedAllPlayerTurnRule,
    ResolvedTurnLandManaTrigger,
)
from ..continuous_effect_state import ResolutionEffectSource, commit_continuous_effect
from ..errors import GameRuleError
from ..rules.all_player_turn_rules import (
    decode_all_player_turn_rule,
    decode_controller_turn_hexproof,
    decode_turn_land_mana_trigger,
    uses_turn_land_mana_trigger,
)


def apply_all_player_turn_rule(host, effect, *, actor, operation, reason):
    del operation, reason
    host._require_seat(actor, in_game=True)
    try:
        source = ResolutionEffectSource.from_effect(effect)
        instruction = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
        if uses_turn_land_mana_trigger(instruction):
            spec = decode_turn_land_mana_trigger(instruction, source=source.stack_ref)
            return commit_turn_land_mana_trigger(host, actor, source, spec)
        if instruction.get("schema_version") == 2:
            decode_controller_turn_hexproof(instruction, source=source.stack_ref, player=actor)
            rule, player = AllPlayerTurnRule.HEXPROOF, actor
        else:
            rule, player = decode_all_player_turn_rule(instruction, source.stack_ref), None
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    resolved = ResolvedAllPlayerTurnRule(
        host._next_ref("PR"), source.stack_ref, host._next_zone_timestamp(), rule, player=player,
    )
    commit_continuous_effect(host.state, resolved)
    payload = {"effect": resolved.effect_id, "rule": rule.value}
    if player is None:
        host._log(actor, "rule.all_players.turn.created", "A turn rule applies to all players.", payload, importance=1)
    else:
        host._log(actor, "rule.player.turn.created", f"{player} gained hexproof until end of turn.",
                  {**payload, "player": player}, importance=1)
    return resolved.effect_id


def commit_turn_land_mana_trigger(host, actor, source, spec):
    stack_item = host.state.stack[-1] if host.state.stack else None
    if (stack_item is None or stack_item.ref != source.stack_ref or stack_item.controller != actor
            or stack_item.kind not in {"spell", "spell_copy"} or not stack_item.context.get("currently_resolving")):
        raise ValueError("A turn mana trigger must come from its resolving spell")
    card = host.state.cards.get(stack_item.card_object_id)
    super_types = (
        host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))[2]
        if card is not None else ()
    )
    resolved = ResolvedTurnLandManaTrigger(
        host._next_ref("PR"), source.stack_ref, host._next_zone_timestamp(), spec, "snow" in super_types,
    )
    commit_continuous_effect(host.state, resolved)
    host._log(actor, "rule.all_players.turn.created", "A temporary land mana trigger applies.",
              {"effect": resolved.effect_id, "land_subtype": spec.land_subtype, "mana": spec.symbol}, importance=1)
    return resolved.effect_id
