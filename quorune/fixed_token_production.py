from __future__ import annotations

"""Closed descriptors shared by fixed token-producing Oracle grammar."""

from dataclasses import dataclass
from typing import Any, Mapping


FIXED_TOKEN_PRODUCTION_MECHANIC_ID = "fixed-token-production"
INVESTIGATE_CAPABILITY_ID = "keyword_action.investigate.fixed"
INVESTIGATE_MECHANIC_ID = "investigate"
SEQUENTIAL_INVESTIGATE_OPERATION = "investigate_sequential"
AFTERLIFE_CAPABILITY_ID = "trigger.keyword.afterlife.fixed"
AFTERLIFE_MECHANIC_ID = "afterlife"
FIXED_TOKEN_COPY_CAPABILITY_ID = "token.creation.fixed_copy"
FIXED_DELAYED_TOKEN_CAPABILITY_ID = "trigger.delayed.fixed_token_creation"
TYPED_TOKEN_ABILITY_CAPABILITY_ID = "token.creation.typed_ability_definition"
TYPED_TOKEN_ABILITY_MECHANIC_ID = "fixed-token-typed-ability-definition"


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


def fixed_token_player_target_schema(relation: str) -> dict[str, Any]:
    """Describe the single player who creates an ordinary fixed token."""
    if relation not in ("any", "opponent"):
        raise FixedTokenProductionError("Unsupported token creator target relation")
    return {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": relation,
        "count": 1,
    }


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
        if self.count == 1:
            return clue_token_effect(1)
        return {
            "op": SEQUENTIAL_INVESTIGATE_OPERATION,
            "schema_version": self.schema_version,
            "controller": "$controller",
            "count": self.count,
        }


def sequential_investigate_count(
    effect: Mapping[str, Any], *, resolved_controller: str | None = None,
) -> int | Mapping[str, Any]:
    """Validate a fixed or typed count before, and again after, amount resolution."""
    controller = "$controller" if resolved_controller is None else resolved_controller
    if (
        not isinstance(effect, Mapping)
        or set(effect) != {"op", "schema_version", "controller", "count"}
        or effect["op"] != SEQUENTIAL_INVESTIGATE_OPERATION
        or effect["controller"] != controller
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != 1
    ):
        raise FixedTokenProductionError("Sequential Investigate requires a closed controller and count")
    count = effect["count"]
    if resolved_controller is not None:
        if type(count) is not int or count < 0:
            raise FixedTokenProductionError("Resolved Investigate requires a nonnegative count")
        return count
    if type(count) is int and _fixed_count(count, label="Investigate") >= 2:
        return count
    if isinstance(count, Mapping) and _known_investigate_amount(count):
        return count
    raise FixedTokenProductionError("Sequential Investigate requires a fixed or represented count")


def _known_investigate_amount(count: Mapping[str, Any]) -> bool:
    from .characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from .query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec
    from .scalar_effect_amount_model import SCALAR_AMOUNT_KIND, ScalarAmountOrigin, ScalarEffectAmountSpec

    try:
        if count.get("kind") == SCALAR_AMOUNT_KIND:
            return ScalarEffectAmountSpec.from_dict(count) == ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
        if count.get("kind") == PUBLIC_QUERY_AMOUNT_KIND:
            amount = PublicQueryAmountSpec.from_dict(count)
            opponents = CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.CONTROLLER_OPPONENT_COUNT,
                schema_version=2,
            )
            return amount.schema_version == 2 and amount.coefficient == 1 and amount.quantity == opponents
    except (TypeError, ValueError):
        return False
    return False


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
    "SEQUENTIAL_INVESTIGATE_OPERATION",
    "sequential_investigate_count",
    "TYPED_TOKEN_ABILITY_CAPABILITY_ID",
    "TYPED_TOKEN_ABILITY_MECHANIC_ID",
    "afterlife_token_effect",
    "clue_token_effect",
]
