from __future__ import annotations

from enum import Enum


class ImpulseAccessDuration(str, Enum):
    END_OF_TURN = "until_end_of_turn"
    END_OF_NEXT_TURN = "until_end_of_next_turn"


__all__ = ["ImpulseAccessDuration"]
