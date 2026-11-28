"""Resolve fixed library-top exile through the canonical zone owner."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..linked_ability_exile_state import exile_for_linked_ability
from ..rules.linked_color_cast_exile import LINKED_COLOR_TOP_EXILE, LinkedColorCastExile


def apply_exile_top_library_card(host, effect, *, actor, operation, reason):
    from ..rules.linked_landfall_play import EXPLORE_EXILE_FIELD, apply_exploration_exile

    if EXPLORE_EXILE_FIELD in effect:
        return apply_exploration_exile(host,effect,actor=actor,reason=reason)
    if LINKED_COLOR_TOP_EXILE in effect:
        return apply_linked_color_cast_exile(host, effect, actor=actor, reason=reason)
    if set(effect) not in (
        {"op", "player"},
        {"op", "player", "_runtime_source"},
    ):
        raise GameRuleError("Top-library exile effect is malformed")
    seat = str(effect.get("player") or "")
    if seat not in host.active_seats:
        raise GameRuleError("Top-library exile player is unavailable")
    library = host.state.players[seat].zones["library"]
    if not library:
        return None
    card = host.state.cards[library[-1]]
    moved = host.move_card(
        card.object_id,
        "exile",
        reason=reason,
        semantic_events=True,
    )
    host._log(
        actor,
        "library.ingest",
        f"{seat} moved the top card of their library to {moved.zone}.",
        {
            "player": seat,
            "object": moved.ref,
            "destination": moved.zone,
            "reason": reason,
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[seat],
    )
    return moved.ref


def apply_linked_color_cast_exile(host, effect, *, actor, reason):
    source_context = effect.get("_runtime_source")
    stack_ref = source_context.get("stack_ref") if isinstance(source_context, Mapping) else None
    item = next((candidate for candidate in host.state.stack if candidate.ref == stack_ref), None)
    instruction = {key: value for key, value in effect.items()
                   if key not in {"reason", "_runtime_source", "_replacement_selections"}}
    try:
        spec = LinkedColorCastExile.from_effect(instruction, resolved=True)
        player = instruction["player"]
        if (item is None or item.kind != "triggered_ability" or item.controller != actor
                or item.context.get("currently_resolving") is not True
                or item.targets != [player] or player not in host.active_seats):
            raise ValueError("Linked top exile requires its resolving trigger and original player target")
        source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    library = host.state.players[player].zones["library"]
    if not library:
        return None
    moved = exile_for_linked_ability(
        host, card=host.state.cards[library[-1]], source=source, ability_pair=spec.binding_id,
        reason=reason, replacement_selections=effect.get("_replacement_selections", ()),
    )
    return moved[0] if moved else None
