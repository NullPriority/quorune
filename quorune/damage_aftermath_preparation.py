from __future__ import annotations

"""Prepare existing damage modifiers and immediate prevention results together."""

from dataclasses import replace
from typing import Any

from .counter_prevention import prepare_counter_prevention_results, projected_counter_prevention_host
from .damage_prevention import plan_damage_modifier_commit, project_damage_modifier_snapshot
from .damage_prevention_aftermath import (
    prepare_combined_prevention_aftermath,
    prepare_prevention_aftermath,
)
from .damage_values import DamageError
from .replacement import ReplacementChoiceRequired


def prepare_damage_aftermath(host: Any, events, effects, current_modifiers, *, damage_port,
                            selections, sources, source_zones, depth, effect_chain,
                            requests, prepared_results, require_all_selections):
    """Retain the exact applied static-source snapshot, including after redirection."""
    try:
        modifier_plan = plan_damage_modifier_commit(host, events, snapshot=current_modifiers)
        counter_removals = prepare_counter_prevention_results(host, events, effects)
        port = damage_port.with_counter_prevention(counter_removals)
        modifiers = project_damage_modifier_snapshot(current_modifiers, modifier_plan)
        if requests is not None:
            if requests.damage:
                port = port.with_damage_results(prepared_results, modifiers)
            aftermath = prepare_combined_prevention_aftermath(
                requests, damage_port=port, selections=selections, sources=sources,
                source_zones=source_zones, modifier_snapshot=modifiers, depth=depth,
                effect_chain=effect_chain, require_all_selections=require_all_selections,
            )
        else:
            aftermath = prepare_prevention_aftermath(
                projected_counter_prevention_host(host, counter_removals), events,
                damage_port=port, effects=effects, selections=selections, sources=sources, source_zones=source_zones,
                modifier_snapshot=modifiers, depth=depth, effect_chain=effect_chain,
            )
        return modifier_plan, replace(aftermath, counter_removals=counter_removals)
    except ReplacementChoiceRequired as exc:
        if requests is not None:
            raise
        raise DamageError(str(exc)) from exc
    except ValueError as exc:
        raise DamageError(str(exc)) from exc
