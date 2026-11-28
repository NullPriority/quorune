"""Commit the targeting rule through the original resolving ability."""

from ..continuous_effect_state import resolution_effect_source
from ..errors import GameRuleError
from ..turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_WINDOW, validate_turn_creature_target_prohibition,
)


def apply_turn_creature_target_prohibition(host, effect, *, actor, operation, reason):
    from ..rules.targeted_turn_target_prohibition import marks_targeted_turn_prohibition

    if marks_targeted_turn_prohibition(effect):
        return _apply_targeted_turn_prohibition(host, effect, actor=actor, reason=reason)
    instruction = {key: value for key, value in effect.items() if key != "_runtime_source"}
    try:
        validate_turn_creature_target_prohibition(instruction, actor=actor)
        source = resolution_effect_source(host, effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("The turn-long targeting rule requires its original resolving ability")
    host._require_seat(actor, in_game=True)
    host.state.players[actor].stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW] = host.state.turn_sequence
    host._log(
        actor, "effect.turn_creature_target_prohibition",
        f"{actor}'s creatures can't be targeted by opponents this turn.",
        {"player": actor, "reason": reason}, importance=2, changed_players=[actor],
    )
    return True

def _apply_targeted_turn_prohibition(host, effect, *, actor, reason):
    from ..rules.targeted_turn_target_prohibition import (
        CAPABILITY, WINDOW, grant_targeted_turn_prohibition, targeted_turn_prohibition,
    )

    ref = effect.get("target")
    actual = {key: value for key, value in effect.items() if key != "_runtime_source"}
    try:
        if (type(ref) is not str or not ref or ref.startswith("$")
                or type(actual.get("schema_version")) is not int
                or actual != targeted_turn_prohibition(player=actor, target=ref)):
            raise ValueError("The targeted turn prohibition lost its resolving controller or creature")
        source = resolution_effect_source(host, effect)
        item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
        target = next((card for card in host.state.cards.values() if card.ref == ref), None)
        program = host.semantics.get(item.semantic_key) if item is not None else None
        if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
                or item.targets != [ref] or item.context.get("targets_revalidated") is not True
                or item.context.get("currently_resolving") is not True
                or program is None or CAPABILITY not in program.capability_dependencies
                or not host.semantic_program_is_current_trusted(program)
                or target is None or target.zone != "battlefield" or target.phased_out):
            raise ValueError("The targeted turn prohibition requires one revalidated target of its resolving spell")
        host._require_seat(actor, in_game=True)
        stats = host.state.players[actor].stats
        grant = grant_targeted_turn_prohibition(stats, host.state.turn_sequence, target)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    stats[WINDOW] = grant
    host._log(actor, "effect.turn_targeted_creature_prohibition", f"{target.ref} can't be targeted by {actor}'s opponents this turn.",
              {"target": target.ref, "player": actor, "reason": reason}, importance=2, changed_players=[actor])
    return True
