from __future__ import annotations

"""Capture phase-in after the complete status batch and bind the original spell target."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from .phase_occurrence_model import PHASE_IN_EVENT, SPELL_STACK_KINDS, PhaseInOccurrence
from .target_announcements import TARGET_ANNOUNCEMENT_EVENT


def collect_phase_in_triggers(host, incoming):
    if not incoming:
        return ()
    from ..trigger_processing import collect_trigger_items

    occurrences = []
    for identity in incoming:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.logical_object_id != identity.logical_object_id
                or card.zone != "battlefield" or card.phased_out):
            raise GameRuleError("Phase-in requires its committed current battlefield incarnation")
        _, subtypes, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
        occurrences.append(PhaseInOccurrence(card.ref, card.controller, card.logical_object_id, tuple(sorted(subtypes))))
    triggers = []
    for occurrence in occurrences:
        triggers.extend(collect_trigger_items(host, PHASE_IN_EVENT, occurrence.to_context()))
    return tuple(triggers)


def phase_out_original_spell_target(host, *, actor, reason):
    from ..phasing import phase_out_permanent
    from .phase_occurrence_shapes import spell_target_phase_out_runtime_dependencies

    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True or item.targets
            or item.context.get("event") != TARGET_ANNOUNCEMENT_EVENT
            or item.context.get("stack_kind") not in SPELL_STACK_KINDS):
        raise GameRuleError("Occurrence phase-out requires its original resolving untargeted trigger")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not spell_target_phase_out_runtime_dependencies(program)):
        raise GameRuleError("Occurrence phase-out requires its admitted spell-target trigger")
    ref, identity = item.context.get("card"), item.context.get("card_object_identity")
    if any(type(value) is not str or not value for value in (ref, identity)):
        raise GameRuleError("Occurrence phase-out requires its original affected object")
    try:
        card = host._resolve_object(actor, ref, zones={"battlefield"})
    except GameRuleError:
        return None
    if card.logical_object_id != identity:
        return None
    return phase_out_permanent(host, ContinuousObjectIdentity(card.object_id, identity), actor=actor, reason=reason)
