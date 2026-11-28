"""Validate the immutable Scry and Surveil intent arrangements."""

from ..rules.library_scry import ScryArrangement
from ..rules.library_surveillance import SurveilArrangement
from .zone_move_intent import _freeze_replacement_selections


def validate_scry_intent(self) -> None:
    if not isinstance(self.arrangement, ScryArrangement):
        raise TypeError("Scry intents require an immutable arrangement")
    if self.requested_count is not None and (
        type(self.requested_count) is not int or self.requested_count <= 0
    ):
        raise ValueError("Scry requested count must be a positive integer")


def validate_surveil_intent(self) -> None:
    if any(
        type(value) is not str or not value
        for value in (self.actor, self.player, self.reason)
    ):
        raise ValueError(
            "Surveil intents require an actor, player, and reason"
        )
    if not isinstance(self.arrangement, SurveilArrangement):
        raise TypeError("Surveil intents require an immutable arrangement")
    if type(self.requested_count) is not int or self.requested_count <= 0:
        raise ValueError(
            "Surveil intents require a positive fixed count"
        )
    object.__setattr__(
        self,
        "replacement_selections",
        _freeze_replacement_selections(
            tuple(self.replacement_selections),
            family="Surveil",
        ),
    )
