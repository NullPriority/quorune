"""Immutable creation intent, including versioned created-token cleanup."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from ..created_token_cleanup import CreatedTokenCleanupPlan
from ..replacement.immutable import FrozenMap
from .zone_move_intent import _freeze_replacement_selections


@dataclass(frozen=True, slots=True)
class CreateTokenIntent:
    actor: str
    controller: str
    name: str
    quantity: int
    reason: str
    characteristics: FrozenMap = field(default_factory=FrozenMap)
    copy_of: str | None = None
    copy_snapshot: FrozenMap | None = None
    temporary_keywords: tuple[str, ...] = ()
    tapped: bool = False
    attacking_assignments: tuple[str, ...] = ()
    attacking_groups: tuple[str, ...] = ()
    sacrifice_at_end_step: bool = False
    sacrifice_on_controller_end_step: bool = False
    exile_at_end_of_combat: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    token_cleanup: CreatedTokenCleanupPlan | None = None

    def __post_init__(self) -> None:
        if self.token_cleanup is not None and (
                not isinstance(self.token_cleanup, CreatedTokenCleanupPlan)
                or self.copy_of is not None or self.copy_snapshot is not None
                or self.sacrifice_at_end_step or self.sacrifice_on_controller_end_step or self.exile_at_end_of_combat):
            raise ValueError('Typed token cleanup cannot mix with copy recipes or legacy lifecycle flags')
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.controller, self.reason)
        ):
            raise ValueError(
                "Token-creation intents require actor, controller, and reason"
            )
        if type(self.quantity) is not int or self.quantity < 0:
            raise ValueError("Token-creation quantity must be nonnegative")
        if self.copy_of is not None and (
            type(self.copy_of) is not str or not self.copy_of
        ):
            raise ValueError("Token-copy source must be nonempty or null")
        if self.copy_snapshot is not None:
            if self.copy_of is None:
                raise ValueError("A token copy snapshot requires a copy source")
            if not isinstance(self.copy_snapshot, FrozenMap):
                if not isinstance(self.copy_snapshot, Mapping):
                    raise ValueError("Token copy snapshot must be an object")
                object.__setattr__(
                    self, "copy_snapshot", FrozenMap(self.copy_snapshot)
                )
        if type(self.name) is not str or not (self.name or self.copy_of):
            raise ValueError("Token creation requires a name or copy source")
        keywords = tuple(self.temporary_keywords)
        if (
            any(type(value) is not str or not value for value in keywords)
            or len(keywords) != len(set(keywords))
        ):
            raise ValueError(
                "Temporary token keywords must be unique nonempty strings"
            )
        if (
            type(self.sacrifice_at_end_step) is not bool
            or type(self.sacrifice_on_controller_end_step) is not bool
            or type(self.exile_at_end_of_combat) is not bool
            or type(self.tapped) is not bool
        ):
            raise ValueError("Token sacrifice flags must be booleans")
        assignments = tuple(self.attacking_assignments)
        groups = tuple(self.attacking_groups)
        malformed_plan = (
            any(
                type(value) is not str or not value
                for value in (*assignments, *groups)
            )
            or (groups and len(groups) != self.quantity)
            or (not groups and assignments and len(assignments) != self.quantity)
            or (groups and len(assignments) < self.quantity)
        )
        if malformed_plan:
            raise ValueError("Attacking token plan is malformed")
        object.__setattr__(self, "temporary_keywords", keywords)
        object.__setattr__(self, "attacking_assignments", assignments)
        object.__setattr__(self, "attacking_groups", groups)
        if not isinstance(self.characteristics, FrozenMap):
            object.__setattr__(
                self,
                "characteristics",
                FrozenMap(self.characteristics),
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Token creation",
            ),
        )

