"""Commit a choice-owned public zone movement through the existing rules owner."""

from ..errors import GameRuleError
from ..public_zone_moves import PublicZoneMoveError, resolve_public_zone_move_set
from ..replacement.immutable import thaw_value


def commit_public_zone_move_intent(host, intent):
    try:
        return resolve_public_zone_move_set(
            host,
            actor=intent.actor,
            followup=intent.followup,
            spec=intent.spec,
            reason=intent.reason,
            source_ref=intent.source_ref,
            aura_target_refs=thaw_value(intent.aura_target_refs) if intent.aura_target_refs is not None else None,
            replacement_selections=tuple(
                thaw_value(value)
                for value in intent.replacement_selections
            ),
        )
    except PublicZoneMoveError as exc:
        raise GameRuleError(str(exc)) from exc
