"""Schedule and consume next-draw grants through the existing duration journal."""

from ..continuous_effect_state import commit_continuous_effect, consume_continuous_effects
from ..next_draw_replacement_model import ResolvedNextDrawReplacement
from ..rules.next_draw_replacement_shapes import require_scheduled_draw_result_ownership
from ..replacement import ReplacementClass, ReplacementEffect
from ..replacement.operations import ReplaceDrawWithScheduledResult
from .model import DrawError, PreparedDrawEvent, validate_prepared_draw


def schedule_next_draw_replacement(state, grant: ResolvedNextDrawReplacement) -> ResolvedNextDrawReplacement:
    if not isinstance(grant, ResolvedNextDrawReplacement):
        raise DrawError("The next draw requires one typed scheduled replacement")
    require_scheduled_draw_result_ownership(grant.result)
    return commit_continuous_effect(state, grant)


def _draw_replacement(grant: ResolvedNextDrawReplacement) -> ReplacementEffect:
    return ReplacementEffect(
        effect_id=f"next-draw-result:{grant.effect_id}",
        source_id=grant.source_id,
        event_kind="draw",
        replacement_class=ReplacementClass.OTHER,
        conditions={"is_draw": {"eq": True}, "affected_player": {"eq": grant.controller}},
        operations=(ReplaceDrawWithScheduledResult(grant.effect_id),),
        label=f"{grant.source_id} replaces the next draw with its scheduled result",
    )


def collect_scheduled_draw_replacements(state, player: str) -> tuple[ReplacementEffect, ...]:
    return tuple(
        _draw_replacement(grant)
        for grant in state.continuous_effects or ()
        if isinstance(grant, ResolvedNextDrawReplacement) and grant.controller == player
    )


def consume_scheduled_draw_result(state, prepared: PreparedDrawEvent, *, apnap_order) -> ResolvedNextDrawReplacement:
    validate_prepared_draw(prepared, apnap_order=apnap_order)
    resolution = prepared.resolution
    if resolution is None or resolution.kind != "scheduled_result":
        raise DrawError("No committed scheduled replacement belongs to this draw")
    grant = next(
        (
            entry
            for entry in state.continuous_effects or ()
            if isinstance(entry, ResolvedNextDrawReplacement)
            and entry.effect_id == resolution.scheduled_grant_id
            and entry.controller == resolution.player
        ),
        None,
    )
    if grant is None:
        raise DrawError("The applied next-draw grant is unavailable")
    replacement = _draw_replacement(grant)
    if replacement.effect_id not in prepared.event.applied_effects or replacement not in prepared.effects:
        raise DrawError("The draw does not carry its exact scheduled replacement identity")
    consume_continuous_effects(state, (grant.effect_id,))
    return grant
