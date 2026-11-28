from __future__ import annotations

"""Terminal resolving-spell moves over independently closed bodies."""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ConditionalSpellReturn(str, Enum):
    OPPONENT_LIFE = "opponent_life"
    TARGET_OR_CONTROLLER_LIFE = "target_or_controller_life"
    OPPONENT_HAND = "opponent_hand"
    TARGET_HAND = "target_hand"
    OPPONENT_CREATURES = "opponent_creatures"


@dataclass(frozen=True, slots=True)
class TerminalSpellMove:
    destination: str
    capability: str
    mechanic: str
    template: str
    condition: ConditionalSpellReturn | None = None


SELF_EXILE = TerminalSpellMove("exile", "zone.exile.resolving_spell",
    "spell-resolution-self-exile", "terminal-spell-self-exile-v1")
SELF_SHUFFLE = TerminalSpellMove("library", "zone.shuffle.resolving_spell",
    "spell-resolution-self-shuffle", "terminal-spell-self-shuffle-v1")
CAPABILITY, MECHANIC, TEMPLATE = SELF_EXILE.capability, SELF_EXILE.mechanic, SELF_EXILE.template
SELF_BOTTOM = TerminalSpellMove("library", "zone.library_bottom.resolving_spell",
    "spell-resolution-self-bottom", "terminal-spell-self-bottom-v1")
CONDITIONAL_SPELL_RETURN_CAPABILITY = "zone.hand.conditional_resolving_spell"
CONDITIONAL_SPELL_RETURN_MOVES = {
    condition: TerminalSpellMove(
        "graveyard", CONDITIONAL_SPELL_RETURN_CAPABILITY,
        f"spell-resolution-conditional-hand-{condition.value.replace('_', '-')}",
        f"terminal-spell-conditional-hand-{condition.value.replace('_', '-')}-v1",
        condition,
    )
    for condition in ConditionalSpellReturn
}
CONDITIONAL_TARGET_LIFE_MECHANIC = CONDITIONAL_SPELL_RETURN_MOVES[
    ConditionalSpellReturn.TARGET_OR_CONTROLLER_LIFE
].mechanic
_MOVES = (SELF_EXILE, SELF_SHUFFLE, SELF_BOTTOM, *CONDITIONAL_SPELL_RETURN_MOVES.values())
TERMINAL_SPELL_MOVE_CAPABILITIES = {move.mechanic: (move.capability,) for move in _MOVES}
TERMINAL_SPELL_MOVE_MECHANICS = frozenset(TERMINAL_SPELL_MOVE_CAPABILITIES)


def terminal_spell_move(mechanics) -> TerminalSpellMove | None:
    matches = [move for move in _MOVES if move.mechanic in mechanics]
    return matches[0] if len(matches) == 1 else None


def _conditional_return_target_dependencies(move, target_schema):
    if move.condition is None:
        return ()
    if move.condition is ConditionalSpellReturn.TARGET_OR_CONTROLLER_LIFE:
        from ..replacement.immutable import FrozenMap, thaw_value
        from ..target_controller_reference import TARGET_CONTROLLER_INFORMATION_CAPABILITY

        if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
            return None
        expected = {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
                    "predicate": "player_or_planeswalker", "count": 1}
        if thaw_value(FrozenMap(target_schema)) != expected:
            return None
        return (TARGET_CONTROLLER_INFORMATION_CAPABILITY,)
    if move.condition is ConditionalSpellReturn.TARGET_HAND:
        from ..replacement.immutable import FrozenMap, thaw_value

        expected = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
        if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
            return None
        return () if thaw_value(FrozenMap(target_schema)) == expected else None
    return () if target_schema is None else None


def terminal_spell_move_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    move = terminal_spell_move(mechanics)
    if move is None or not effects:
        return ()
    from .paid_creature_library_search import paid_creature_search_node_capabilities

    paid_search = paid_creature_search_node_capabilities(
        effects=effects, target_schema=target_schema, mechanics=mechanics, cost_schema=cost_schema,
    )
    if paid_search is not None:
        return paid_search
    from .targeted_opponent_protection import MECHANIC as OPPONENT_PROTECTION_MECHANIC

    scope = (() if move == SELF_EXILE and OPPONENT_PROTECTION_MECHANIC in mechanics
             else _conditional_return_target_dependencies(move, target_schema))
    if scope is None or (move.condition is not None and cost_schema is not None):
        return ()
    from .capabilities import capability_dependencies_for_node
    body = capability_dependencies_for_node(effects=effects,target_schema=target_schema,
        mechanic_ids=set(mechanics)-{move.mechanic},cost_schema=cost_schema)
    return tuple(sorted({move.capability, *scope, *body})) if body else ()


def _move_runtime_dependencies(program, move):
    marked = move.mechanic in program.coverage
    wrapped = move.template in str(program.provenance.get('template_id') or '').split('+')
    if not marked and not wrapped:
        return ()
    if (not marked or not wrapped or program.destination != move.destination
            or getattr(program,'event',None) != 'resolve'
            or getattr(program,'active_zone',None) != 'stack'
            or not str(getattr(program,'ability_id','')).startswith('spell:')):
        action = move.mechanic.removeprefix("spell-resolution-").replace("-", " ")
        raise ValueError(f'Terminal {action} requires its complete resolving-spell context')
    return (move.capability,)


def conditional_spell_return_runtime_dependencies(program):
    matches = [
        move for move in CONDITIONAL_SPELL_RETURN_MOVES.values()
        if _move_runtime_dependencies(program, move)
    ]
    if not matches:
        return ()
    if len(matches) != 1 or terminal_spell_move(program.coverage) is not matches[0]:
        raise ValueError("A conditional terminal spell requires one original comparison")
    scope = _conditional_return_target_dependencies(matches[0], program.target_schema)
    if scope is None or program.cost_schema is not None or program.handlers or program.event_condition is not None:
        raise ValueError("A conditional terminal spell requires its exact original body and target domain")
    return (CONDITIONAL_SPELL_RETURN_CAPABILITY, *scope)


def _conditional_return_matches(host, item, condition):
    players = host.state.players
    actor = item.controller
    if condition is ConditionalSpellReturn.TARGET_OR_CONTROLLER_LIFE:
        from ..target_controller_information import resolve_target_controller_information
        from ..target_controller_reference import target_controller_reference

        target, = item.targets
        seat = target if target in players else resolve_target_controller_information(
            host, target_controller_reference(0), item,
        )
        return seat in players and players[seat].life > players[actor].life
    if condition is ConditionalSpellReturn.TARGET_HAND:
        target, = item.targets
        return target in players and len(players[target].zones["hand"]) > len(players[actor].zones["hand"])
    opponents = tuple(seat for seat in host.active_seats if seat != actor)
    if condition is ConditionalSpellReturn.OPPONENT_LIFE:
        return any(players[seat].life > players[actor].life for seat in opponents)
    if condition is ConditionalSpellReturn.OPPONENT_HAND:
        return any(len(players[seat].zones["hand"]) > len(players[actor].zones["hand"]) for seat in opponents)
    if condition is ConditionalSpellReturn.OPPONENT_CREATURES:
        from ..affected_permanents import AffectedPermanentSetSpec, select_affected_permanents
        from ..object_predicate import ObjectQuerySpec

        creatures = select_affected_permanents(
            host.affected_permanent_object_rows(actor),
            AffectedPermanentSetSpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))),
            actor=actor, active_seats=host.affected_permanent_active_seats(),
            apnap_order=host.affected_permanent_apnap_order(),
        )
        counts = Counter(row.controller for row in creatures)
        return any(counts[seat] > counts[actor] for seat in opponents)
    raise ValueError("Unsupported conditional terminal spell comparison")


def conditional_terminal_spell_destination(host, item, destination):
    """Choose the requested hand move after the body, before normal replacements."""
    if destination not in {None, "graveyard"} or item.kind != "spell" or not item.card_object_id:
        return destination
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    move = terminal_spell_move(program.coverage) if program is not None else None
    if move is None or move.condition is None:
        return destination
    card = host.state.cards.get(item.card_object_id)
    if (card is None or card.zone != "stack" or card.is_spell_copy
            or not host.semantic_program_is_current_trusted(program)):
        return destination
    return "hand" if _conditional_return_matches(host, item, move.condition) else destination


def spell_self_exile_runtime_dependencies(program):
    return _move_runtime_dependencies(program, SELF_EXILE)


def spell_self_shuffle_runtime_dependencies(program):
    return _move_runtime_dependencies(program, SELF_SHUFFLE)


def spell_self_bottom_runtime_dependencies(program):
    return _move_runtime_dependencies(program, SELF_BOTTOM)
