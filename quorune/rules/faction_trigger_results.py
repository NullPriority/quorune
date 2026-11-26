"""Bind faction results to their sealed trigger and draw occurrences."""

from ..drawing.transaction import begin_faction_draw, faction_draw_reward
from .faction_trigger_model import faction_event, validate_faction_instruction
from .faction_trigger_shapes import faction_branch_for_program


def prepare_faction_derived_effect(host, item, effect):
    choice, result = validate_faction_instruction(effect)
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (
        program is None or program.trust_level != "trusted" or item.kind != "triggered_ability"
        or item.context.get("currently_resolving") is not True or item.targets
        or item.context.get("event") != faction_event(choice)
        or faction_branch_for_program(program) != choice
    ):
        raise ValueError("A faction result requires its original resolving trigger")
    if result == "opponents_draw":
        return begin_faction_draw(item)
    if result == "controller_draw":
        return faction_draw_reward(item)
    player = item.context.get("player")
    defender = item.context.get("defender")
    count = item.context.get("attacker_count")
    if (type(player) is not str or type(defender) is not str or player == defender
            or player not in host.state.players or defender not in host.state.players
            or type(count) is not int or count < 1):
        raise ValueError("An Enclave result requires its declaration against the original controller")
    if player not in host.active_seats:
        return None
    source = host.state.cards.get(item.source_object_id)
    return {
        "op": "place_player_counters", "subjects": "target", "target": player,
        "counter": "rad", "amount": 2 * count, "source": source.ref if source is not None else item.ref,
    }
