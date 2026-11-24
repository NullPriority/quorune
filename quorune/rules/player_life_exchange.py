from __future__ import annotations

"""Closed two-player life exchange requests prepared from one observed total pair."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..life_gain_permission import LifeGainPermissionSnapshot
from ..life_state import LifeChange
from ..replacement.immutable import thaw_value


PLAYER_LIFE_EXCHANGE_OPERATION = "exchange_two_life_totals"
PLAYER_LIFE_EXCHANGE_CAPABILITY = "life.change.exchange_two_totals"
PLAYER_LIFE_EXCHANGE_RULES = ("119.7", "119.8", "608.2b", "701.12a", "701.12c")
_PLAYER_LIFE_EXCHANGE_DEPENDENCIES = (
    PLAYER_LIFE_EXCHANGE_CAPABILITY, "life.change.effect", "target.revalidate_resolution",
)


@dataclass(frozen=True, slots=True)
class PlayerLifeExchangeSpec:
    scope: str
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.scope) is not str or self.scope not in {"two_targets", "controller_and_opponent"}
            or type(self.schema_version) is not int or self.schema_version != 1
        ):
            raise ValueError("A life-total exchange requires its closed two-player scope")

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "scope"}:
            raise ValueError("Life-total exchange has unknown fields")
        return cls(**dict(value))

    def to_target_schema(self):
        two_targets = self.scope == "two_targets"
        return {"zones": ["player"], "categories": ["player"], "count": 2 if two_targets else 1,
                "player_relation": "any" if two_targets else "opponent"}

    def to_instruction(self):
        players = ["$target.0", "$target.1"] if self.scope == "two_targets" else ["$controller", "$target.0"]
        return {"op": PLAYER_LIFE_EXCHANGE_OPERATION, "exchange": {"schema_version": self.schema_version, "scope": self.scope},
                "players": players}


def _require_player_life_exchange(effect, schema):
    spec = PlayerLifeExchangeSpec.from_mapping(effect.get("exchange"))
    expected = spec.to_target_schema()
    if (
        effect.get("op") != PLAYER_LIFE_EXCHANGE_OPERATION
        or thaw_value(effect) != spec.to_instruction()
        or thaw_value(schema) != expected or type(schema["count"]) is not int
    ):
        raise ValueError("A life exchange requires its exact participants and target cardinality")
    return spec


def player_life_exchange_instruction_dependencies(effects, target_schema):
    effect, = effects
    _require_player_life_exchange(effect, target_schema)
    return _PLAYER_LIFE_EXCHANGE_DEPENDENCIES


def player_life_exchange_program_scope(program):
    from .cumulative_object_upkeep import CONTROLLER_UPKEEP

    carrier = (program.event, program.active_zone)
    if carrier == ("resolve", "stack"):
        from .post_exchange_token import validate_post_exchange_token_instruction

        exchange, token = program.effects
        spec = _require_player_life_exchange(exchange, program.target_schema)
        validate_post_exchange_token_instruction(token)
        if spec.scope != "two_targets":
            raise ValueError("A post-exchange token requires two target players")
        return spec, False
    effect, = program.effects
    if carrier == ("activate", "battlefield"):
        return _require_player_life_exchange(effect, program.target_schema), False
    if carrier == ("step.begin", "battlefield") and thaw_value(program.event_condition) == CONTROLLER_UPKEEP:
        inner, = effect.get("effects", ())
        spec = _require_player_life_exchange(inner, program.target_schema)
        expected = {"op": "offer_optional_effect", "player": "$controller", "effects": [spec.to_instruction()]}
        if spec.scope == "two_targets" and thaw_value(effect) == expected:
            return spec, True
    raise ValueError("Life exchange requires a complete activation or optional own-upkeep trigger")


def player_life_exchange_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from .post_exchange_token import POST_EXCHANGE_TOKEN_CAPABILITY, POST_EXCHANGE_TOKEN_VERSION_FIELD

    if not any(effect.get("op") == PLAYER_LIFE_EXCHANGE_OPERATION or POST_EXCHANGE_TOKEN_VERSION_FIELD in effect
               for effect in nested_effects((program.effects, program.handlers, program.target_schema))):
        return ()
    _, optional = player_life_exchange_program_scope(program)
    if program.event == "resolve":
        return (*_PLAYER_LIFE_EXCHANGE_DEPENDENCIES, POST_EXCHANGE_TOKEN_CAPABILITY, "token.creation.fixed_definition")
    return (*_PLAYER_LIFE_EXCHANGE_DEPENDENCIES, *(("effect.choice.optional_fixed",) if optional else ()))


def is_closed_optional_player_life_exchange_program(program):
    if program.event != "step.begin":
        return False
    try:
        required = player_life_exchange_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def plan_player_life_exchange(
    first: str, second: str, lives: Mapping[str, int], gain_permissions: LifeGainPermissionSnapshot,
) -> tuple[LifeChange, ...] | None:
    """Return both life instructions, or omit the whole exchange when a half is unavailable."""
    if not isinstance(gain_permissions, LifeGainPermissionSnapshot):
        raise ValueError("Exchange requires current typed life gain permissions")
    if type(first) is not str or type(second) is not str or first == second:
        return None
    if first not in lives or second not in lives:
        return None
    one, two = lives[first], lives[second]
    if type(one) is not int or type(two) is not int:
        raise ValueError("Exchange requires two integer life totals")
    if one == two:
        return ()
    recipient = first if one < two else second
    if not gain_permissions.allows_gain(recipient):
        return None
    return LifeChange(first, two - one), LifeChange(second, one - two)
