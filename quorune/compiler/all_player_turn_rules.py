"""Printed all-player rules with a fixed cleanup duration."""

from ..all_player_turn_rule_model import AllPlayerTurnRule
from ..rules.all_player_turn_rules import ALL_PLAYER_TURN_RULE_MECHANIC, all_player_turn_rule_instruction


_HEXPROOF = "players gain hexproof until end of turn"
_NO_LIFE_LOSS = "players can't lose life this turn"
_NO_GAME_OUTCOME = "players can't lose the game or win the game this turn"
_ALL_PLAYER_TURN_CLAUSES = {
    _HEXPROOF: (AllPlayerTurnRule.HEXPROOF,),
    _NO_LIFE_LOSS: (AllPlayerTurnRule.NO_LIFE_LOSS,),
    _NO_GAME_OUTCOME: (AllPlayerTurnRule.NO_GAME_OUTCOME,),
    f"{_NO_LIFE_LOSS} and {_NO_GAME_OUTCOME}": (
        AllPlayerTurnRule.NO_LIFE_LOSS, AllPlayerTurnRule.NO_GAME_OUTCOME,
    ),
}


def all_player_turn_rules_template(text):
    clause = " ".join(text.casefold().replace("’", "'").split()).removesuffix(".")
    rules = _ALL_PLAYER_TURN_CLAUSES.get(clause)
    if rules is None:
        return None
    return (
        "all-player-turn-rules-v1", tuple(all_player_turn_rule_instruction(rule) for rule in rules),
        None, (ALL_PLAYER_TURN_RULE_MECHANIC,),
    )
