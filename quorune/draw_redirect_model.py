"""Resolution-created target-player draw replacement and its journal record."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError


DRAW_REDIRECT_OPERATION = "redirect_draws_until_end_of_turn"
DRAW_REDIRECT_KIND = "resolved_draw_redirect"
DRAW_REDIRECT_MECHANIC = "temporary-target-player-draw-redirect"
DRAW_REDIRECT_CAPABILITY = "zone.draw.result_redirect_until_end_of_turn"
DRAW_REDIRECT_REQUIREMENTS = (
    "target.revalidate_resolution",
    "zone.draw.result_generated_ordering",
    DRAW_REDIRECT_CAPABILITY,
)


def draw_redirect_target_schema() -> dict[str, Any]:
    return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}


def draw_redirect_instruction() -> dict[str, Any]:
    return {"op": DRAW_REDIRECT_OPERATION, "schema_version": 1, "player": "$target.0"}


def temporary_draw_redirect_node_capabilities(*, effects, target_schema, mechanic_ids, **_):
    if (
        DRAW_REDIRECT_MECHANIC in mechanic_ids
        and len(effects) == 1
        and effects[0] == draw_redirect_instruction()
        and type(effects[0]["schema_version"]) is int
        and target_schema == draw_redirect_target_schema()
    ):
        return DRAW_REDIRECT_REQUIREMENTS
    return ()


def temporary_draw_redirect_runtime_dependencies(program):
    def contains(value):
        if isinstance(value, Mapping):
            return value.get("op") == DRAW_REDIRECT_OPERATION or any(contains(item) for item in value.values())
        if isinstance(value, (tuple, list)):
            return any(contains(item) for item in value)
        return False

    if not contains((program.effects, program.handlers)):
        return ()
    required = temporary_draw_redirect_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required or program.handlers:
        raise ValueError("Draw redirection requires one resolving instruction and its revalidated player target")
    return required


@dataclass(frozen=True, slots=True)
class TemporaryDrawRedirectIntent:
    actor: str
    target_player: str
    source_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.actor, self.target_player, self.source_id)):
            raise ValueError("Draw redirection requires the resolving controller, target player, and source")


@dataclass(frozen=True, slots=True)
class ResolvedDrawRedirect:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    target_player: str
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if (
            any(type(value) is not str or not value for value in (
                self.effect_id, self.source_id, self.controller, self.target_player
            ))
            or type(self.timestamp) is not int
            or self.timestamp < 0
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
        ):
            raise ContinuousEffectError("Temporary draw replacement requires its identities and end-of-turn duration")

    def to_dict(self):
        return {
            "effect_kind": DRAW_REDIRECT_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "controller": self.controller, "target_player": self.target_player,
            "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp",
                  "controller", "target_player", "duration"}
        if (
            not isinstance(value, Mapping)
            or set(value) != fields
            or value["effect_kind"] != DRAW_REDIRECT_KIND
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
        ):
            raise ContinuousEffectError("Malformed temporary draw replacement")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"],
            value["controller"], value["target_player"], ContinuousEffectDuration(value["duration"]),
        )

    @property
    def fingerprint(self):
        from .replacement.immutable import immutable_fingerprint

        return immutable_fingerprint(self.to_dict())
