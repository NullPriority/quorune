from __future__ import annotations

"""Terminal resolving-spell moves over independently closed bodies."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TerminalSpellMove:
    destination: str
    capability: str
    mechanic: str
    template: str


SELF_EXILE = TerminalSpellMove("exile", "zone.exile.resolving_spell",
    "spell-resolution-self-exile", "terminal-spell-self-exile-v1")
SELF_SHUFFLE = TerminalSpellMove("library", "zone.shuffle.resolving_spell",
    "spell-resolution-self-shuffle", "terminal-spell-self-shuffle-v1")
CAPABILITY, MECHANIC, TEMPLATE = SELF_EXILE.capability, SELF_EXILE.mechanic, SELF_EXILE.template
_MOVES = (SELF_EXILE, SELF_SHUFFLE)
TERMINAL_SPELL_MOVE_MECHANICS = frozenset(move.mechanic for move in _MOVES)


def terminal_spell_move(mechanics) -> TerminalSpellMove | None:
    matches = [move for move in _MOVES if move.mechanic in mechanics]
    return matches[0] if len(matches) == 1 else None


def terminal_spell_move_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    move = terminal_spell_move(mechanics)
    if move is None or not effects:
        return ()
    from .capabilities import capability_dependencies_for_node
    body = capability_dependencies_for_node(effects=effects,target_schema=target_schema,
        mechanic_ids=set(mechanics)-{move.mechanic},cost_schema=cost_schema)
    return tuple(sorted({move.capability, *body})) if body else ()


def _move_runtime_dependencies(program, move):
    marked = move.mechanic in program.coverage
    wrapped = move.template in str(program.provenance.get('template_id') or '').split('+')
    if not marked and not wrapped:
        return ()
    if (not marked or not wrapped or program.destination != move.destination
            or getattr(program,'event',None) != 'resolve'
            or getattr(program,'active_zone',None) != 'stack'
            or not str(getattr(program,'ability_id','')).startswith('spell:')):
        action = 'self exile' if move is SELF_EXILE else 'self shuffle'
        raise ValueError(f'Terminal {action} requires its complete resolving-spell context')
    return (move.capability,)


def spell_self_exile_runtime_dependencies(program):
    return _move_runtime_dependencies(program, SELF_EXILE)


def spell_self_shuffle_runtime_dependencies(program):
    return _move_runtime_dependencies(program, SELF_SHUFFLE)
