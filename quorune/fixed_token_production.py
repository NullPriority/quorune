from __future__ import annotations

"""Closed descriptors shared by fixed token-producing Oracle grammar."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


FIXED_TOKEN_PRODUCTION_MECHANIC_ID = "fixed-token-production"
FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID = "token.creation.fixed_distributed"
FIXED_DISTRIBUTED_TOKEN_MECHANIC_ID = "fixed-distributed-token-creation"
FIXED_DISTRIBUTED_TOKEN_OPERATION = "create_distributed_tokens"
INVESTIGATE_CAPABILITY_ID = "keyword_action.investigate.fixed"
INVESTIGATE_MECHANIC_ID = "investigate"
SEQUENTIAL_INVESTIGATE_OPERATION = "investigate_sequential"
AFTERLIFE_CAPABILITY_ID = "trigger.keyword.afterlife.fixed"
AFTERLIFE_MECHANIC_ID = "afterlife"
FIXED_TOKEN_COPY_CAPABILITY_ID = "token.creation.fixed_copy"
FIXED_DELAYED_TOKEN_CAPABILITY_ID = "trigger.delayed.fixed_token_creation"
TYPED_TOKEN_ABILITY_CAPABILITY_ID = "token.creation.typed_ability_definition"
TYPED_TOKEN_ABILITY_MECHANIC_ID = "fixed-token-typed-ability-definition"
TOKEN_LITERAL_NAME_VERSION_FIELD = "literal_token_name_version"


class FixedTokenProductionError(ValueError):
    """A fixed token-production descriptor exceeds its closed grammar."""


class FixedTokenCreatorScope(str, Enum):
    EACH_PLAYER = "each_player"
    EACH_OPPONENT = "each_opponent"
    CONTROLLER_AND_TARGET_OPPONENT = "controller_and_target_opponent"


def fixed_token_creator_scope(effect: Mapping[str, Any]) -> FixedTokenCreatorScope:
    scope = FixedTokenCreatorScope(effect.get("scope"))
    fields = {"op", "scope", "controller", "token"}
    if scope is FixedTokenCreatorScope.CONTROLLER_AND_TARGET_OPPONENT:
        fields.add("target")
    if (set(effect) != fields or effect["op"] != FIXED_DISTRIBUTED_TOKEN_OPERATION
            or not isinstance(effect["token"], Mapping)):
        raise FixedTokenProductionError("Distributed token creation has a closed effect shape")
    return scope


def fixed_token_effect_shape_is_closed(
    effect: Mapping[str, Any],
) -> bool:
    expected_fields = {
        "op",
        "controller",
        "name",
        "quantity",
        "characteristics",
    }
    if "tapped" in effect:
        expected_fields.add("tapped")
    if set(effect) != expected_fields:
        return False
    if (
        effect.get("op") != "create_token"
        or effect.get("controller") != "$controller"
    ):
        return False
    if type(effect["name"]) is not str or not effect["name"] or not isinstance(effect["characteristics"], Mapping):
        return False
    quantity = effect.get("quantity")
    if type(quantity) is not int or quantity <= 0:
        return False
    return "tapped" not in effect or effect.get("tapped") is True


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


def literal_token_name_version(values: Mapping[str, Any]) -> int | None:
    """Validate the name authority carried by a fixed token definition."""
    if TOKEN_LITERAL_NAME_VERSION_FIELD not in values:
        return None
    version = values[TOKEN_LITERAL_NAME_VERSION_FIELD]
    if type(version) is not int or version != 1:
        raise FixedTokenProductionError("Unsupported literal token name version")
    return version


def fixed_ordinary_token_name(subtypes: str) -> str:
    """Derive a name from the printed subtype list when no name was given."""
    return f"{subtypes} Token" if subtypes else "Token"


def predefined_token_identity(
    subtype: str, characteristics: Mapping[str, Any], *, legacy_name: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Materialize the name of an authored unnamed predefined artifact."""
    if legacy_name:
        return subtype, dict(characteristics)
    return fixed_ordinary_token_name(subtype), {
        **characteristics,
        TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
    }


def historical_unnamed_token_view(
    name: object, definition: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Compare a corrected definition to its former default-name spelling."""
    try:
        version = literal_token_name_version(definition)
    except FixedTokenProductionError:
        return None
    type_line = definition.get("type_line")
    if version is None or not isinstance(type_line, str):
        return None
    _, separator, subtypes = type_line.partition(" — ")
    if not separator or not subtypes or name != fixed_ordinary_token_name(subtypes):
        return None
    former = dict(definition)
    del former[TOKEN_LITERAL_NAME_VERSION_FIELD]
    return subtypes, former


def _fixed_count(value: object, *, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 20:
        raise FixedTokenProductionError(
            f"{label} count must be an integer from one to twenty"
        )
    return value


def clue_token_effect(quantity: int, *, legacy_name: bool = False) -> dict[str, Any]:
    """Return the canonical fixed Clue definition for Investigate."""

    name, characteristics = predefined_token_identity("Clue", {
        "type_line": "Token Artifact — Clue",
        "display_text": "{2}, Sacrifice this token: Draw a card.",
        "activated_ability_profile": "two_sac_draw_card_v1",
    }, legacy_name=legacy_name)
    return {
        "op": "create_token",
        "controller": "$controller",
        "name": name,
        "quantity": _fixed_count(quantity, label="Investigate"),
        "characteristics": characteristics,
    }


def afterlife_token_effect(quantity: int, *, legacy_name: bool = False) -> dict[str, Any]:
    """Return the canonical Spirit definition for one Afterlife instance."""

    return {
        "op": "create_token",
        "controller": "$controller",
        "name": "Spirit" if legacy_name else fixed_ordinary_token_name("Spirit"),
        "quantity": _fixed_count(quantity, label="Afterlife"),
        "characteristics": {
            "type_line": "Token Creature — Spirit",
            **({} if legacy_name else {TOKEN_LITERAL_NAME_VERSION_FIELD: 1}),
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
