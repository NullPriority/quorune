"""Bind a one-point replacement to its resolving activation and target."""

from ..continuous_effect_state import ResolutionEffectSource
from ..damage import recipient_snapshot
from ..damage_modifier_state import DamageSubject
from ..errors import GameRuleError
from ..rules.finite_damage_redirection import (
    finite_damage_redirection_instruction,
    finite_damage_redirection_runtime_dependencies,
)


def finite_damage_redirection_subjects(host, effect, *, actor):
    instruction = finite_damage_redirection_instruction()
    if (
        set(effect) - {"_runtime_source", "_replacement_selections", "reason"} != set(instruction)
        or type(effect["maximum_damage"]) is not int
        or effect["maximum_damage"] != 1
    ):
        raise GameRuleError("One-point redirection requires its complete bound instruction")
    source = ResolutionEffectSource.from_effect(effect)
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    if (
        item is None or item.kind != "activated_ability" or item.controller != actor
        or item.context.get("currently_resolving") is not True or item.source_object_id != source.object_id
        or item.context.get("source_logical_object_id") != source.logical_object_id
        or item.context.get("targets_revalidated") is not True or len(item.targets) != 1
        or item.targets[0] != effect["destination"] or source.card_ref != effect["source"]
        or source.card_ref != effect["subject"]
    ):
        raise GameRuleError("One-point redirection requires its own resolving source and revalidated target")
    program = host.semantics.get(item.semantic_key)
    if (
        program is None or not host.semantic_program_is_current_trusted(program)
        or not finite_damage_redirection_runtime_dependencies(program)
    ):
        raise GameRuleError("One-point redirection requires its own trusted activation")
    card = host.state.cards.get(source.object_id)
    if card is None or card.zone != "battlefield" or card.logical_object_id != source.logical_object_id:
        return None
    if card.ref != source.card_ref:
        raise GameRuleError("The one-point redirection source changed")
    destination = recipient_snapshot(host, item.targets[0], actor=actor)
    if destination.controller != actor or "creature" not in destination.types:
        raise GameRuleError("One-point redirection requires a creature the activating player controls")
    return (
        DamageSubject(card.ref, "permanent", card.controller, card.object_id, card.logical_object_id, card.owner),
        DamageSubject(destination.ref, destination.kind, destination.controller,
                      destination.object_id, destination.logical_object_id, destination.owner),
    )
