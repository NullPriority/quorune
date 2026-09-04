from __future__ import annotations

"""Closed descriptors shared by fixed token-producing Oracle grammar."""

from dataclasses import dataclass
from typing import Any, Mapping


FIXED_TOKEN_PRODUCTION_MECHANIC_ID = "fixed-token-production"
INVESTIGATE_CAPABILITY_ID = "keyword_action.investigate.fixed"
INVESTIGATE_MECHANIC_ID = "investigate"
AFTERLIFE_CAPABILITY_ID = "trigger.keyword.afterlife.fixed"
AFTERLIFE_MECHANIC_ID = "afterlife"
FIXED_TOKEN_COPY_CAPABILITY_ID = "token.creation.fixed_copy"
FIXED_DELAYED_TOKEN_CAPABILITY_ID = "trigger.delayed.fixed_token_creation"


class FixedTokenProductionError(ValueError):
    """A fixed token-production descriptor exceeds its closed grammar."""


@dataclass(frozen=True, slots=True)
class FixedTokenCreationTemplate:
    """One compiler-closed token instruction and its typed dependencies."""

    template_id: str
    effect: Mapping[str, Any]
    mechanics: tuple[str, ...]
    target_schema: Mapping[str, Any] | None = None

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            (self.effect,),
            self.target_schema,
            self.mechanics,
        )


def _fixed_count(value: object, *, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 20:
        raise FixedTokenProductionError(
            f"{label} count must be an integer from one to twenty"
        )
    return value


def clue_token_effect(quantity: int) -> dict[str, Any]:
    """Return the canonical fixed Clue definition for Investigate."""

    return {
        "op": "create_token",
        "controller": "$controller",
        "name": "Clue",
        "quantity": _fixed_count(quantity, label="Investigate"),
        "characteristics": {
            "type_line": "Token Artifact — Clue",
            "display_text": "{2}, Sacrifice this token: Draw a card.",
            "activated_ability_profile": "two_sac_draw_card_v1",
        },
    }


def afterlife_token_effect(quantity: int) -> dict[str, Any]:
    """Return the canonical Spirit definition for one Afterlife instance."""

    return {
        "op": "create_token",
        "controller": "$controller",
        "name": "Spirit",
        "quantity": _fixed_count(quantity, label="Afterlife"),
        "characteristics": {
            "type_line": "Token Creature — Spirit",
            "colors": ["W", "B"],
            "power": "1",
            "toughness": "1",
            "keywords": ["Flying"],
        },
    }


@dataclass(frozen=True, slots=True)
class FixedInvestigateSpec:
    count: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedTokenProductionError(
                "Unsupported Investigate descriptor version"
            )
        _fixed_count(self.count, label="Investigate")

    def effect(self) -> dict[str, Any]:
        return clue_token_effect(self.count)


@dataclass(frozen=True, slots=True)
class FixedAfterlifeSpec:
    count: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedTokenProductionError(
                "Unsupported Afterlife descriptor version"
            )
        _fixed_count(self.count, label="Afterlife")

    def effect(self) -> dict[str, Any]:
        return afterlife_token_effect(self.count)


__all__ = [
    "AFTERLIFE_CAPABILITY_ID",
    "AFTERLIFE_MECHANIC_ID",
    "FIXED_DELAYED_TOKEN_CAPABILITY_ID",
    "FIXED_TOKEN_COPY_CAPABILITY_ID",
    "FIXED_TOKEN_PRODUCTION_MECHANIC_ID",
    "FixedAfterlifeSpec",
    "FixedTokenCreationTemplate",
    "FixedInvestigateSpec",
    "FixedTokenProductionError",
    "INVESTIGATE_CAPABILITY_ID",
    "INVESTIGATE_MECHANIC_ID",
    "afterlife_token_effect",
    "clue_token_effect",
]
