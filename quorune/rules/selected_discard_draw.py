"""Bind one private opponent discard round to its committed draw count."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
from .effect_shape_walkers import contains_nested_key


SELECTED_DISCARD_DRAW_FIELD = "selected_discard_draw_result"
SELECTED_DISCARD_DRAW_CAPABILITY = "zone.discard.opponent_choice_committed_draw"
SELECTED_DISCARD_DRAW_MECHANIC = "selected-opponent-discard-draw-result"


def selected_discard_draw_choice(*, actor="$controller"):
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": "opponents", "zone": "hand",
        "predicate": ObjectQuerySpec(zones=("hand",)).to_dict(), "count": 1, "then": "discard",
        "hidden": True, "prompt": "Choose the required card(s) to discard.", SELECTED_DISCARD_DRAW_FIELD: 1,
    }


def validate_selected_discard_draw_choice(effect, actor):
    if (
        not isinstance(effect, Mapping) or type(effect.get("count")) is not int
        or type(effect.get(SELECTED_DISCARD_DRAW_FIELD)) is not int
        or not isinstance(actor, str) or not actor
        or FrozenMap(effect) != FrozenMap(selected_discard_draw_choice(actor=actor))
    ):
        raise ValueError("A committed discard draw requires exactly one private choice per opponent")


def selected_discard_draw_node_dependencies(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect, = effects
    try:
        validate_selected_discard_draw_choice(effect, "$controller")
    except (TypeError, ValueError):
        return ()
    base = {key: value for key, value in effect.items() if key != SELECTED_DISCARD_DRAW_FIELD}
    required = fixed_affected_player_discard_node_capabilities(
        effects=(base,), target_schema=None, mechanic_ids=("fixed-affected-player-discard", "cr-402-hand"),
    )
    return (SELECTED_DISCARD_DRAW_CAPABILITY, "zone.draw.library_to_hand", *required) if required else ()


def selected_discard_draw_program_dependencies(program):
    if not (contains_nested_key((program.effects, program.handlers), SELECTED_DISCARD_DRAW_FIELD)
            or SELECTED_DISCARD_DRAW_CAPABILITY in program.capability_dependencies):
        return ()
    required = selected_discard_draw_node_dependencies(program.effects, program.target_schema)
    if (
        not required or (program.event, program.active_zone) != ("resolve", "stack")
        or program.handlers or program.cost_schema is not None or program.event_condition is not None
    ):
        raise ValueError("A committed opponent discard result requires its whole untargeted resolving spell")
    return required


def is_closed_selected_discard_draw_program(program):
    try:
        required = selected_discard_draw_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _require_resolving_spell(host, item):
    program = host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (
        item is None or item.kind not in {"spell", "spell_copy"} or item.targets
        or item.context.get("currently_resolving") is not True
        or program is None or not host.semantic_program_is_current_trusted(program)
        or not is_closed_selected_discard_draw_program(program)
    ):
        raise ValueError("The committed discard receipt lost its original resolving spell")


def validate_resolving_selected_discard_choice(host, item, effect):
    _require_resolving_spell(host, item)
    validate_selected_discard_draw_choice(effect, item.controller)


def selected_discard_draw_followup(host, item, effect, selected_refs):
    if SELECTED_DISCARD_DRAW_FIELD not in effect or not selected_refs:
        return ()
    validate_resolving_selected_discard_choice(host, item, effect)
    cards = tuple(host._resolve_object(item.controller, ref, zones={"hand"}) for ref in selected_refs)
    members = tuple(sorted((card.owner, card.logical_object_id) for card in cards))
    if len({owner for owner, _ in members}) != len(members) or any(
        owner not in host.active_seats or owner == item.controller or not identity for owner, identity in members
    ):
        raise ValueError("The discard receipt must seal one current hand incarnation per opponent")
    return ({"op": "draw", SELECTED_DISCARD_DRAW_FIELD: {
        "schema_version": 1, "stack_ref": item.ref, "controller": item.controller,
        "history_cursor": len(tuple(host._current_turn_history("card_discarded"))), "members": members,
    }},)


def bind_selected_discard_draw(host, item, effect):
    from .discard_results import committed_discard_members

    _require_resolving_spell(host, item)
    raw = effect[SELECTED_DISCARD_DRAW_FIELD]
    if (set(effect) != {"op", SELECTED_DISCARD_DRAW_FIELD} or effect["op"] != "draw"
            or not isinstance(raw, Mapping)
            or set(raw) != {"schema_version", "stack_ref", "controller", "history_cursor", "members"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["stack_ref"] != item.ref or raw["controller"] != item.controller):
        raise ValueError("The committed discard receipt has an invalid stack frame")
    members = raw["members"]
    events = tuple(host._current_turn_history("card_discarded"))
    cursor = raw["history_cursor"]
    if (
        type(cursor) is not int or not 0 <= cursor <= len(events)
        or not isinstance(members, (list, tuple)) or not members
        or any(not isinstance(pair, (list, tuple)) or len(pair) != 2
               or any(type(value) is not str or not value for value in pair)
               or pair[0] not in host.seats or pair[0] == item.controller for pair in members)
        or len({pair[0] for pair in members}) != len(members)
        or tuple(map(tuple, members)) != tuple(sorted(map(tuple, members)))
    ):
        raise ValueError("The committed discard receipt has malformed opponent identities")
    count = len(committed_discard_members(events[cursor:], members))
    return {"op": "draw", "player": item.controller, "count": count, "private": True} if count else None
