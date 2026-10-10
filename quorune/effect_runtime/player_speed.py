from __future__ import annotations

from ..errors import GameRuleError
from ..rules.player_speed_model import SpeedIncrease
from ..rules.player_speed_state import commit_speed_increase


def apply_speed_increase(host,effect,*,actor,operation,reason):
    try:
        instruction=SpeedIncrease.from_effect({k:v for k,v in effect.items() if k!='_runtime_source'})
        return commit_speed_increase(host,instruction)
    except (TypeError,ValueError) as exc:raise GameRuleError(str(exc)) from exc
