"""Precedence of retained combat declaration condition forms."""

from __future__ import annotations


def has_legacy_declaration_condition(line: str) -> bool:
    from .declaration_restrictions import (
        _ATTACHED_ATTACKING_ALONE_EVASION,
        _SELF_ALREADY_ATTACKED_PLAYER_THIS_TURN,
        _SELF_ATTACKING_ALONE_EVASION,
        _SELF_BATTLEFIELD_CONDITION,
        _SELF_CAST_SPELL_THIS_TURN,
        _SELF_CONDITIONAL_BLOCKER_FILTER,
        _SELF_CONDITIONAL_UNBLOCKABLE,
        _SELF_CONTROLLED_CREATURE_DIED_THIS_TURN,
        _SELF_NO_OTHER_CREATURE_EVASION,
        _SELF_OPPONENT_DAMAGED_THIS_TURN,
        _SELF_PLAYER_STATE_CONDITION,
        _battlefield_condition,
    )

    legacy_battlefield = _SELF_BATTLEFIELD_CONDITION.fullmatch(line)
    legacy_conditional_evasion = _SELF_CONDITIONAL_UNBLOCKABLE.fullmatch(line)
    return (
        legacy_battlefield is not None
        and _battlefield_condition(
            (
                "source_controller"
                if legacy_battlefield.group("source")
                else "defending_player"
            ),
            legacy_battlefield.group("filter"),
        )
        is not None
    ) or (
        legacy_conditional_evasion is not None
        and _battlefield_condition(
            "defending_player",
            legacy_conditional_evasion.group("condition"),
        )
        is not None
    ) or any(
        pattern.fullmatch(line) is not None
        for pattern in (
            _SELF_PLAYER_STATE_CONDITION,
            _SELF_CAST_SPELL_THIS_TURN,
            _SELF_OPPONENT_DAMAGED_THIS_TURN,
            _SELF_CONTROLLED_CREATURE_DIED_THIS_TURN,
            _SELF_ALREADY_ATTACKED_PLAYER_THIS_TURN,
            _SELF_CONDITIONAL_BLOCKER_FILTER,
            _SELF_ATTACKING_ALONE_EVASION,
            _ATTACHED_ATTACKING_ALONE_EVASION,
            _SELF_NO_OTHER_CREATURE_EVASION,
        )
    )
