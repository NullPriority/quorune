"""Apply player attack restrictions through the declaration and effect owners."""

from ..continuous_effect_state import commit_continuous_effect
from ..next_turn_attack_restriction_model import NextTurnAttackRestrictionIntent, ResolvedNextTurnAttackRestriction


def apply_next_turn_attack_restriction(host, intent: NextTurnAttackRestrictionIntent) -> tuple[str, ...]:
    players = host.state.players
    host._require_seat(intent.actor, in_game=True)
    if any(seat not in players for seat in intent.opponents):
        raise ValueError("A next-turn attack restriction names an unknown opponent")
    result = []
    for seat in intent.opponents:
        player = players[seat]
        if not player.in_game:
            continue
        effect = ResolvedNextTurnAttackRestriction(
            host._next_ref("CE"), intent.source_id, host._next_zone_timestamp(),
            seat, intent.actor, player.turns_begun + 1,
        )
        commit_continuous_effect(host.state, effect)
        result.append(effect.effect_id)
        host._log(
            intent.actor, "combat.declaration_restriction",
            f"{seat} can't attack {intent.actor} or permanents they control during {seat}'s next turn.",
            {"player": seat, "protected_player": intent.actor, "own_turn": effect.own_turn,
             "continuous_effect": effect.effect_id},
            importance=1, changed_players=[seat, intent.actor],
        )
    return tuple(result)


def apply_until_protected_turn_attack_restrictions(host, *, source_id, protected_player, accepting_players):
    for player in accepting_players:
        if player == protected_player:
            continue
        effect = commit_continuous_effect(host.state, ResolvedNextTurnAttackRestriction(
            host._next_ref("CE"), source_id, host._next_zone_timestamp(), player, protected_player, None,
        ))
        host._log(
            protected_player, "combat.declaration_restriction",
            f"{player} can't attack {protected_player} or their planeswalkers until {protected_player}'s next turn.",
            {"player": player, "protected_player": protected_player, "continuous_effect": effect.effect_id},
            importance=1, changed_players=[player, protected_player],
        )


def attack_declaration_targets(host, attacker, planeswalkers, battles) -> list[str]:
    from .equipped_planeswalker import equipped_planeswalker_cannot_be_attacked

    restrictions = tuple(effect for effect in host.state.continuous_effects or ()
                         if isinstance(effect, ResolvedNextTurnAttackRestriction)
                         and effect.applies(host.state, attacker))
    protected = {effect.protected_player for effect in restrictions}
    legacy_protected = {effect.protected_player for effect in restrictions if effect.own_turn is not None}
    walker_ids = {walker["id"] for walker in planeswalkers}
    recipients = [
        *((seat, seat) for seat in host.active_seats if seat != attacker),
        *((walker["id"], walker["controller"]) for walker in planeswalkers),
        *((battle["id"], battle["controller"]) for battle in battles if battle["id"] not in walker_ids),
    ]
    return [
        reference for reference, controller in recipients
        if controller not in (protected if reference in host.active_seats or reference in walker_ids else legacy_protected)
        and (reference in host.active_seats or not equipped_planeswalker_cannot_be_attacked(host, reference))
    ]
