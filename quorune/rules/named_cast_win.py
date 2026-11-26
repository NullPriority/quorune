from __future__ import annotations

"""Close the single non-targeted hand-cast win or library-and-life result."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..resolution_conditions import (
    RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION,
)
from ..spell_cast_name_model import (
    CAST_NAME_RECEIPT_FIELD, SPELL_CAST_NAME_HISTORY_CAPABILITY, SpellCastNameReceipt,
)
from ..spell_resolution_names import stack_spell_rules_names
from .upkeep_game_win import PLAYER_WIN_CAPABILITY


NAMED_CAST_WIN_FIELD = "named_cast_win"
NAMED_CAST_WIN_TEMPLATE = "named-hand-cast-game-win-or-library-v1"
NAMED_CAST_WIN_CAPABILITY = "spell.resolve.named_hand_cast_win"
NAMED_CAST_WIN_CAPABILITIES = (
    NAMED_CAST_WIN_CAPABILITY, SPELL_CAST_NAME_HISTORY_CAPABILITY, PLAYER_WIN_CAPABILITY,
    RESOLUTION_CONDITION_CAPABILITY, "life.change.effect", "zone.change.destination_replacement",
)


def named_cast_win_effect(name):
    return {"op": RESOLUTION_CONDITION_OPERATION, "player": "$controller",
            NAMED_CAST_WIN_FIELD: {"schema_version": 1, "name": name}}


def uses_named_cast_win(effect):
    return isinstance(effect, Mapping) and NAMED_CAST_WIN_FIELD in effect


def _condition_name(effect):
    condition = effect.get(NAMED_CAST_WIN_FIELD)
    if (not isinstance(condition, Mapping) or set(condition) != {"schema_version", "name"}
            or type(condition["schema_version"]) is not int or condition["schema_version"] != 1
            or type(condition["name"]) is not str or not condition["name"]
            or condition["name"].strip() != condition["name"]):
        raise ValueError("A named cast result requires its exact printed name")
    return condition["name"]


def _unbound_name(effect):
    name = _condition_name(effect)
    if FrozenMap(effect) != FrozenMap(named_cast_win_effect(name)):
        raise ValueError("A named cast result must be a single closed controller instruction")
    return name


def named_cast_win_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (target_schema is not None or cost_schema is not None or len(effects) != 1
            or RESOLUTION_CONDITION_MECHANIC not in mechanic_ids):
        return ()
    try:
        _unbound_name(effects[0])
    except (TypeError, ValueError):
        return ()
    return NAMED_CAST_WIN_CAPABILITIES


def named_cast_win_runtime_dependencies(program):
    if (not any(uses_named_cast_win(effect) for effect in program.effects)
            and NAMED_CAST_WIN_CAPABILITY not in program.capability_dependencies):
        return ()
    owned = named_cast_win_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
        mechanic_ids=program.coverage,
    )
    if (not owned or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.event_condition is not None or not program.ability_id.startswith("spell:")
            or program.provenance.get("template_id") != NAMED_CAST_WIN_TEMPLATE):
        raise ValueError("A named cast result requires its complete resolving spell")
    return owned


def bind_named_cast_win(host, effect, item):
    name = _unbound_name(effect)
    if item.kind not in {"spell", "spell_copy"}:
        raise ValueError("A named cast result needs its original stack spell")
    program = host.semantics.get(item.semantic_key)
    if program is None or not named_cast_win_runtime_dependencies(program):
        raise ValueError("A named cast result needs its trusted spell program")
    if not set(NAMED_CAST_WIN_CAPABILITIES) <= set(program.capability_dependencies):
        raise ValueError("A named cast result lost its capability authority")
    ledger = host.state.spell_resolution_name_ledger
    if ledger is None or ledger.cast_history is None:
        raise ValueError("Complete named cast history is unavailable")
    history = ledger.cast_history
    card = host.state.cards.get(item.card_object_id)
    if (card is None or card.zone != "stack"
            or name not in stack_spell_rules_names(card, host._effective_card_data(card))):
        raise ValueError("A named cast result lost its resolving spell name or object")
    won = False
    if item.kind == "spell":
        receipt = SpellCastNameReceipt.from_dict(item.context.get(CAST_NAME_RECEIPT_FIELD))
        if (receipt.stack_ref != item.ref or receipt.caster not in host.state.players
                or receipt.hand_owner is not None and receipt.hand_owner != card.owner or name not in receipt.names
                or history.count(receipt.caster, name) < 1):
            raise ValueError("A named cast result lost its committed cast identity")
        other = history.count(item.controller, name) - int(receipt.caster == item.controller)
        won = receipt.hand_owner == item.controller and other > 0
    return {**effect, "player": item.controller, "win": won, "card": card.ref}


def named_cast_win_bound_results(effect, *, actor, stack_controller):
    _condition_name(effect)
    if (set(effect) != {"op", "player", NAMED_CAST_WIN_FIELD, "win", "card"}
            or effect["op"] != RESOLUTION_CONDITION_OPERATION
            or effect["player"] != actor or actor != stack_controller or type(effect["win"]) is not bool
            or type(effect["card"]) is not str or not effect["card"]):
        raise ValueError("A named cast result requires the bound controller and stack card")
    if effect["win"]:
        return ({"op": "win_game", "player": actor},)
    move = {"op": "move_if_in_zone", "card": effect["card"], "from": "stack",
            "destination": "library", "position": 7}
    return (move, {"op": "life", "player": actor, "delta": 7})
