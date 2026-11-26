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


def attack_declaration_targets(host, attacker, planeswalkers, battles) -> list[str]:
    protected = {
        effect.protected_player for effect in host.state.continuous_effects or ()
        if isinstance(effect, ResolvedNextTurnAttackRestriction) and effect.applies(host.state, attacker)
    }
    walker_ids = {walker["id"] for walker in planeswalkers}
    recipients = [
        *((seat, seat) for seat in host.active_seats if seat != attacker),
        *((walker["id"], walker["controller"]) for walker in planeswalkers),
        *((battle["id"], battle["controller"]) for battle in battles if battle["id"] not in walker_ids),
    ]
    return [reference for reference, controller in recipients if controller not in protected]
