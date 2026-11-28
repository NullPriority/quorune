"""Use the committed grantor activation to bind one finite recipient and destination."""

from ..continuous_effect_state import ResolutionEffectSource
from ..damage import recipient_snapshot
from ..damage_modifier_state import DamageSubject
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..rules.grantor_damage_redirection import (
    FIELD, grantor_redirect_activation_dependencies, grantor_redirect_effect,
    grantor_redirect_grant, validate_grantor_redirect_grant_child,
)


def verify_resolving_grantor_redirect(host, fragment, bound):
    if bound is None:
        raise GameRuleError("A grantor-only ability requires its resolving spell")
    actor, source, target, instruction = bound
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    value = fragment["value"]
    if (
        item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
        or item.context.get("currently_resolving") is not True
        or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (target.ref,)
        or target.controller != actor or actor not in host.active_seats
        or program is None or not host.semantic_program_is_current_trusted(program)
        or value["activation_grantor"] != actor
        or thaw_value(instruction) != grantor_redirect_grant(value["ability_id"], value["semantic_key"],
                                                            activator=actor, target=target.ref)
        or thaw_value(program.effects) != [grantor_redirect_grant(value["ability_id"], value["semantic_key"])]
    ):
        raise GameRuleError("The original resolving grant must retain its controller and target")
    validate_grantor_redirect_grant_child(program, host.semantics.get)


def grantor_damage_redirection_subjects(host, effect, *, actor):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    actual = {key: value for key, value in effect.items() if key not in {"_runtime_source", "_replacement_selections", "reason"}}
    if (item is None or item.kind != "activated_ability" or item.controller != actor
            or item.context.get("activation_grantor") != actor or actor not in host.active_seats
            or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True
            or item.source_object_id != source.object_id or item.context.get("source_logical_object_id") != source.logical_object_id
            or len(item.targets) != 1 or item.targets[0] is None
            or actual != {**grantor_redirect_effect(), "subject": item.targets[0],
                          "source": source.card_ref, "destination": source.card_ref}
            or type(actual[FIELD]) is not int or type(actual["maximum_damage"]) is not int):
        raise GameRuleError("One-point redirection requires the original resolved grantor activation")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not grantor_redirect_activation_dependencies(program)):
        raise GameRuleError("One-point redirection requires its independent trusted child")
    destination = host.state.cards.get(source.object_id)
    if (destination is None or destination.zone != "battlefield"
            or destination.logical_object_id != source.logical_object_id):
        return None
    subject = recipient_snapshot(host, item.targets[0], actor=actor)
    if subject.kind != "player" and {"creature", "planeswalker"}.isdisjoint(subject.types):
        raise GameRuleError("The revalidated recipient must be a creature, planeswalker or player")
    return (
        DamageSubject(subject.ref, subject.kind, subject.controller, subject.object_id,
                      subject.logical_object_id, subject.owner),
        DamageSubject(destination.ref, "permanent", destination.controller, destination.object_id,
                      destination.logical_object_id, destination.owner),
    )
