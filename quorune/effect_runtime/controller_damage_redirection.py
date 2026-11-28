"""Bind the controller and permanent incarnation to a resolving native effect."""

from ..continuous_effect_state import ResolutionEffectSource
from ..damage import recipient_snapshot
from ..damage_modifier_state import DamageSubject
from ..errors import GameRuleError
from ..rules.controller_damage_redirection import (
    CHOSEN, CHOICE_FIELD, DESTINATION_TYPES, REDIRECTION_FIELD, TARGETED, redirection_program_dependencies,
)
from ..semantic_runtime.context import semantic_source_context
from ..semantic_runtime.values import resolve_semantic_value


def controller_damage_redirection_subjects(host, effect, *, actor):
    destination_kind = effect[REDIRECTION_FIELD]
    chosen = destination_kind == CHOSEN
    expected = {"op", REDIRECTION_FIELD, "source", "destination", "consume_on_application"}
    if chosen:
        expected.add("destination_incarnation")
    if (
        not isinstance(destination_kind, str) or destination_kind not in DESTINATION_TYPES
        or set(effect) - {"_runtime_source", "_replacement_selections", "reason"} != expected
        or effect["consume_on_application"] is not False
    ):
        raise GameRuleError("Controller damage redirection requires its complete bound instruction")
    source = ResolutionEffectSource.from_effect(effect)
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    kinds = {"spell", "spell_copy"} if chosen or destination_kind == TARGETED else {"triggered_ability"}
    if (
        item is None or item.kind not in kinds or item.controller != actor
        or item.context.get("currently_resolving") is not True or actor not in host.active_seats
        or resolve_semantic_value(host, "$source", item) != effect["source"]
        or semantic_source_context(item, host.state.cards).to_dict() != source.to_dict()
    ):
        raise GameRuleError("Controller damage redirection requires its own resolving controller")
    program = host.semantics.get(item.semantic_key)
    if (
        program is None or not host.semantic_program_is_current_trusted(program)
        or not redirection_program_dependencies(program)
        or (CHOICE_FIELD in program.effects[0]) != chosen
        or not chosen and program.effects[0].get(REDIRECTION_FIELD) != destination_kind
    ):
        raise GameRuleError("Controller damage redirection requires its own trusted effect")
    ref = effect["destination"]
    if not chosen and ref != resolve_semantic_value(host, program.effects[0]["destination"], item):
        raise GameRuleError("Controller damage redirection lost its bound permanent")
    if destination_kind == TARGETED and (
        item.context.get("targets_revalidated") is not True or tuple(item.targets) != (ref,) or ref is None
    ):
        raise GameRuleError("Targeted damage redirection requires the announced and revalidated creature")
    if ref is None and not chosen:
        return None
    if not isinstance(ref, str) or not ref:
        raise GameRuleError("Controller damage redirection requires a permanent reference")
    destination = recipient_snapshot(host, ref, actor=actor)
    if (destination.kind != "permanent" or DESTINATION_TYPES[destination_kind].isdisjoint(destination.types)
            or host.state.cards[destination.object_id].phased_out):
        return None
    if chosen and (
        destination.controller != actor or destination.logical_object_id != effect["destination_incarnation"]
    ):
        raise GameRuleError("The chosen permanent's original controlled incarnation is unavailable")
    if destination_kind == TARGETED and (destination.controller != actor or "creature" not in destination.types):
        raise GameRuleError("Targeted damage redirection requires a current controlled creature")
    return (
        DamageSubject(actor, "any", actor),
        DamageSubject(destination.ref, destination.kind, destination.controller, destination.object_id,
                      destination.logical_object_id, destination.owner),
        source,
    )
