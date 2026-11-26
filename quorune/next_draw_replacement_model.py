"""Typed results and journal identity for the next controller draw."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .discard_effect_origin import DiscardEffectOrigin
from .replacement.immutable import FrozenMap, immutable_fingerprint, thaw_value


NEXT_DRAW_REPLACEMENT_OPERATION = "schedule_controller_next_draw_replacement"
NEXT_DRAW_REPLACEMENT_KIND = "resolved_controller_next_draw_replacement"
NEXT_DRAW_REPLACEMENT_CAPABILITY = "zone.draw.next_controller_result"
NEXT_DRAW_REPLACEMENT_MECHANIC = "controller-next-draw-replacement"
PUBLIC_PERMANENT_RETURN_RESULT = "return_each_players_permanent"
_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class NextDrawResult:
    kind: str
    payload: FrozenMap

    def __post_init__(self) -> None:
        if not isinstance(self.payload, FrozenMap):
            object.__setattr__(self, "payload", FrozenMap(self.payload))
        if self.kind == "discard_each_opponent":
            amount_field = "count"
        elif self.kind == "gain_life":
            amount_field = "amount"
        elif self.kind == "create_token":
            if set(self.payload) != {"token", "token_mechanics"}:
                raise ValueError("A token draw result requires its fixed token definition")
            token = self.payload["token"]
            mechanics = self.payload["token_mechanics"]
            if (
                not isinstance(token, Mapping)
                or token.get("op") != "create_token"
                or token.get("controller") != "$controller"
                or type(token.get("name")) is not str or not token["name"]
                or type(token.get("quantity")) is not int or token["quantity"] != 1
                or not isinstance(token.get("characteristics"), Mapping)
                or not isinstance(mechanics, (tuple, list))
                or any(type(mechanic) is not str for mechanic in mechanics)
            ):
                raise ValueError("A scheduled token needs its complete fixed creation data")
            return
        elif self.kind == PUBLIC_PERMANENT_RETURN_RESULT:
            if self.payload:
                raise ValueError("A scheduled public return always chooses one controlled permanent per player")
            return
        else:
            raise ValueError("Unsupported result for the next draw")
        if set(self.payload) != {amount_field} or type(self.payload[amount_field]) is not int or self.payload[amount_field] < 1:
            raise ValueError("A next-draw result requires a fixed positive quantity")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "payload": thaw_value(self.payload)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NextDrawResult":
        if not isinstance(value, Mapping) or set(value) != {"kind", "payload"} or not isinstance(value["payload"], Mapping):
            raise ValueError("Malformed next-draw result")
        return cls(value["kind"], FrozenMap(value["payload"]))

    def instruction(self) -> dict[str, Any]:
        return {
            "op": NEXT_DRAW_REPLACEMENT_OPERATION,
            "schema_version": _SCHEMA_VERSION,
            "player": "$controller",
            "result": self.to_dict(),
        }

    @classmethod
    def from_instruction(cls, effect: Mapping[str, Any], *, player: str = "$controller") -> "NextDrawResult":
        if (
            not isinstance(effect, Mapping)
            or set(effect) != {"op", "schema_version", "player", "result"}
            or effect["op"] != NEXT_DRAW_REPLACEMENT_OPERATION
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != _SCHEMA_VERSION
            or effect["player"] != player
        ):
            raise ValueError("Malformed controller next-draw instruction")
        result = effect["result"]
        if isinstance(result, Mapping) and result.get("kind") == "create_token":
            payload = result.get("payload")
            token = payload.get("token") if isinstance(payload, Mapping) else None
            if not isinstance(token, Mapping) or token.get("controller") != player:
                raise ValueError("A scheduled token must belong to the resolving controller")
            # Persist the typed definition independently of the player bound to this grant.
            result = {**result, "payload": {
                **payload, "token": {**token, "controller": "$controller"},
            }}
        return cls.from_dict(result)


@dataclass(frozen=True, slots=True)
class ResolvedNextDrawReplacement:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    result: NextDrawResult
    source_ref: str | None = None
    discard_origin: DiscardEffectOrigin | None = None
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.effect_id, self.source_id, self.controller))
            or type(self.timestamp) is not int
            or self.timestamp < 0
            or not isinstance(self.result, NextDrawResult)
            or self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref)
            or self.discard_origin is not None and (
                not isinstance(self.discard_origin, DiscardEffectOrigin)
                or self.discard_origin.controller != self.controller
                or self.discard_origin.stack_ref != self.source_id
            )
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
        ):
            raise ContinuousEffectError("A pending next-draw result requires the exact resolving identity and turn duration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_kind": NEXT_DRAW_REPLACEMENT_KIND,
            "schema_version": _SCHEMA_VERSION,
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "controller": self.controller,
            "duration": self.duration.value,
            "result": self.result.to_dict(),
            "source_ref": self.source_ref,
            "discard_origin": self.discard_origin.to_dict() if self.discard_origin is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResolvedNextDrawReplacement":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "controller", "duration", "result", "source_ref", "discard_origin"}
            or value["effect_kind"] != NEXT_DRAW_REPLACEMENT_KIND
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _SCHEMA_VERSION
            or value["duration"] != ContinuousEffectDuration.UNTIL_END_OF_TURN.value
        ):
            raise ContinuousEffectError("Malformed pending next-draw replacement")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"], value["controller"],
            NextDrawResult.from_dict(value["result"]),
            value["source_ref"],
            DiscardEffectOrigin.from_dict(value["discard_origin"]) if value["discard_origin"] is not None else None,
        )

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class ScheduleNextDrawResultIntent:
    actor: str
    source_id: str
    result: NextDrawResult

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.actor, self.source_id))
            or not isinstance(self.result, NextDrawResult)
        ):
            raise ValueError("A next-draw grant requires one resolving source and controller")
