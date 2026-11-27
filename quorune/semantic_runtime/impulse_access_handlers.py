from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..combat_damage_library_exile import (
    COMBAT_DAMAGE_CAST_EXILE_VERSION, COMBAT_DAMAGE_EXILE_VERSION,
    validate_combat_damage_cast_exile, validate_combat_damage_exile,
)
from ..compiler.impulse_access_templates import IMPULSE_ACCESS_CAPABILITY_ID
from ..defending_player_library_exile import DEFENDING_PLAYER_EXILE_VERSION, validate_defending_player_exile
from ..impulse_access_model import (
    IMPULSE_ACCESS_DECLARED_COUNT_VERSION, SHUFFLED_TOP_FREE_PLAY_VERSION, ImpulseAccessDuration,
    TOP_FREE_INSTANT_SORCERY_VERSION, impulse_access_count_is_legal,
    validate_shuffled_top_free_play, validate_top_free_instant_sorcery,
)
from ..opponent_library_top_model import (
    OPPONENT_HALF_TOP_EXILE_VERSION, OPPONENT_TOP_EXILE_VERSION, OpponentLibraryTopSpec,
    validate_opponent_half_top_exile,
)
from ..land_entry_library_exile_model import LAND_ENTRY_TOP_EXILE_VERSION, validate_land_entry_top_exile
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import ImpulseAccessIntent, IntentPlan


@dataclass(frozen=True, slots=True)
class FixedImpulseAccessHandler:
    handler_id: str = "generic.fixed-impulse-access.v1"
    schema_version: int = 1
    family: str = "effect.impulse-access"
    operation: str = "fixed_impulse_access"
    rule_references: tuple[str, ...] = (
        "400.7",
        "406.1",
        "601.2",
        "701.13a",
    )
    capability_dependencies: tuple[str, ...] = (
        IMPULSE_ACCESS_CAPABILITY_ID,
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {"op", "player", "count", "duration", "reason", "schema_version"}
        if set(effect) - allowed or not {
            "op",
            "player",
            "count",
            "duration",
        }.issubset(effect):
            raise SemanticNodeError(
                "Fixed impulse-access effect has an invalid shape"
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError(
                "Fixed impulse-access operation is unsupported"
            )
        count = effect.get("count")
        version = effect.get("schema_version", 1)
        if version in {SHUFFLED_TOP_FREE_PLAY_VERSION, TOP_FREE_INSTANT_SORCERY_VERSION}:
            validate = (validate_top_free_instant_sorcery if version == TOP_FREE_INSTANT_SORCERY_VERSION
                        else validate_shuffled_top_free_play)
            try:
                validate(
                    {key: value for key, value in effect.items() if key != "reason"}, actor=context.actor,
                )
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            duration = ImpulseAccessDuration.END_OF_TURN
        elif version in {COMBAT_DAMAGE_EXILE_VERSION, COMBAT_DAMAGE_CAST_EXILE_VERSION}:
            validate = (validate_combat_damage_cast_exile
                        if version == COMBAT_DAMAGE_CAST_EXILE_VERSION else validate_combat_damage_exile)
            try:
                validate(
                    {key: value for key, value in effect.items() if key != "reason"}, actor=context.actor,
                )
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            if effect["player"] in context.query.seats and effect["player"] not in context.query.active_seats:
                return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(), result_shape="by_player")
            duration = ImpulseAccessDuration.END_OF_TURN
        elif version in {DEFENDING_PLAYER_EXILE_VERSION, LAND_ENTRY_TOP_EXILE_VERSION}:
            validate_public_exile = (
                validate_defending_player_exile if version == DEFENDING_PLAYER_EXILE_VERSION
                else validate_land_entry_top_exile
            )
            try:
                validate_public_exile(
                    {key: value for key, value in effect.items() if key != "reason"}, actor=context.actor,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            supplier = effect["player"]
            if supplier in context.query.seats and supplier not in context.query.active_seats:
                return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(), result_shape="by_player")
            duration = None
        elif version == OPPONENT_HALF_TOP_EXILE_VERSION:
            try:
                count = validate_opponent_half_top_exile(
                    {key: value for key, value in effect.items() if key != "reason"}, actor=context.actor,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            duration = None
        elif version == OPPONENT_TOP_EXILE_VERSION:
            try:
                spec = OpponentLibraryTopSpec.from_effect(
                    {key: value for key, value in effect.items() if key != "reason"}, actor=context.actor,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            duration = ImpulseAccessDuration.END_OF_TURN if spec.allows_play else None
        else:
            if (not impulse_access_count_is_legal(count, version)
                    or ("schema_version" in effect and (version != IMPULSE_ACCESS_DECLARED_COUNT_VERSION
                        or effect["player"] != context.actor))):
                raise SemanticNodeError("Impulse access requires a count and controller valid for its instruction version")
            try:
                duration = ImpulseAccessDuration(str(effect.get("duration") or ""))
            except ValueError as exc:
                raise SemanticNodeError(
                    "Fixed impulse access requires a supported duration"
                ) from exc
            if version == IMPULSE_ACCESS_DECLARED_COUNT_VERSION and duration not in {
                ImpulseAccessDuration.END_OF_TURN, ImpulseAccessDuration.END_OF_NEXT_TURN,
            }:
                raise SemanticNodeError("Declared impulse access requires its fixed turn duration")
        player = context.query.require_active_seat(
            str(effect.get("player") or context.actor)
        )
        reason = effect.get("reason") or context.default_reason
        if type(reason) is not str or not reason:
            raise SemanticNodeError(
                "Fixed impulse access requires a nonempty reason"
            )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                ImpulseAccessIntent(
                    actor=context.actor,
                    player=player,
                    count=count,
                    duration=duration,
                    reason=reason,
                    schema_version=version,
                ),
            ),
        )


IMPULSE_ACCESS_HANDLERS = (FixedImpulseAccessHandler(),)


__all__ = ["FixedImpulseAccessHandler", "IMPULSE_ACCESS_HANDLERS"]
