from __future__ import annotations

"""Closed Learn action for the Commander outside-the-game rules boundary."""

from collections.abc import Mapping


COMMANDER_LEARN_CAPABILITY = 'keyword_action.learn.commander'
COMMANDER_LEARN_MECHANIC = 'commander-learn'
COMMANDER_LEARN_RULES_PROFILES = frozenset({'commander_duel', 'commander_multiplayer'})


def commander_learn_instruction(player: str = '$controller') -> dict:
    return {'op': 'offer_optional_mana_payment', 'schema_version': 5,
        'player': player, 'action': 'learn', 'rules_scope': 'commander'}


def validate_commander_learn_instruction(effect: Mapping, *, player: str) -> None:
    if (not isinstance(effect, Mapping)
            or type(effect.get('schema_version')) is not int
            or dict(effect) != commander_learn_instruction(player)):
        raise ValueError('Learn requires its exact Commander action schema')
