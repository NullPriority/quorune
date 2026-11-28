from __future__ import annotations

"""Move a selected owned commander and retain its actual battlefield incarnation."""

from ..errors import GameRuleError
from .commander_return_choice import (
    commander_return_runtime_dependencies, is_commander_battlefield_entry_effect,
)
from .reanimation_followup import BattlefieldEntryFollowup, commit_battlefield_entry_followup


FIELD = "selected_commander_battlefield_entry"


def selected_commander_entry_effect(ref, identity, actor):
    return {
        "op": "move", "card": ref, "from": "command", "destination": "battlefield",
        "controller": actor, FIELD: {"schema_version": 1, "logical_object_id": identity},
    }


def apply_selected_commander_entry(host, effect, *, actor, reason):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..effect_runtime.linked_zone_movement import commit_resolved_zone_movement
    from ..zone_trigger_events import ZoneTransitionKind

    try:
        _validate_selected_commander_effect(effect, actor)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
        program = host.semantics.get(item.semantic_key) if item is not None else None
        if (item is None or item.kind != "triggered_ability" or item.controller != actor
                or item.context.get("currently_resolving") is not True
                or program is None or not host.semantic_program_is_current_trusted(program)
                or len(program.effects) != 1 or not is_commander_battlefield_entry_effect(program.effects[0])
                or not commander_return_runtime_dependencies(program)):
            raise ValueError("Selected commander entry requires its original resolving self-entry ability")
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    card = host._resolve_object(actor, effect["card"], zones={"command"}, owned_only=True)
    if (not card.is_commander or not card.commander_designation_id or not card.is_card_object or card.is_token
            or card.logical_object_id != effect[FIELD]["logical_object_id"]):
        raise GameRuleError("Selected commander is no longer the offered command-zone incarnation")
    previous_counter = card.zone_change_counter
    moved = commit_resolved_zone_movement(
        host, card, effect, destination="battlefield", reason=reason, putting_player=actor,
        transition_kind=ZoneTransitionKind.ORDINARY,
    )
    commit_battlefield_entry_followup(
        host, moved, previous_zone_counter=previous_counter, actor=actor, source=source,
        followup=BattlefieldEntryFollowup("zone_object", "command", "next"),
    )
    return moved


def _validate_selected_commander_effect(effect, actor):
    from ..replacement.immutable import FrozenMap

    marker = effect[FIELD]
    identity = marker["logical_object_id"]
    ref = effect["card"]
    base = {name: value for name, value in effect.items() if name not in {
        "_runtime_source", "_replacement_selections", "_aura_target_ref", "reason",
    }}
    if (type(marker["schema_version"]) is not int or type(identity) is not str or not identity
            or type(ref) is not str or not ref
            or FrozenMap(base) != FrozenMap(selected_commander_entry_effect(ref, identity, actor))):
        raise ValueError("Selected commander entry has a malformed selection or movement")
