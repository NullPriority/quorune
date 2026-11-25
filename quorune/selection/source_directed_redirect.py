"""Read the legal single-slot changes to one original source incarnation."""

from __future__ import annotations

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..rules.source_directed_redirect_model import source_directed_redirect_from_effect
from ..stack_target_change_model import REPRESENTED_STACK_TARGET_CHANGE_KINDS
from ..target_source import stack_targeting_source
from ..targets import target_plan
from .copy_targets import original_copy_groups, prepare_copy_targets
from .target_query import target_identity_matches_snapshot


def source_directed_redirect_options(host, *, target_ref: str, source: ContinuousObjectIdentity):
    """Use the target stack item's own controller and preserve its other slots."""
    recipient = host.state.cards.get(source.object_id)
    if (recipient is None or recipient.logical_object_id != source.logical_object_id
            or recipient.zone != "battlefield" or recipient.phased_out):
        return ()
    item = next((entry for entry in host.state.stack if entry.ref == target_ref), None)
    if item is None or item.kind not in REPRESENTED_STACK_TARGET_CHANGE_KINDS or not item.targets:
        return ()
    schema = host._stack_target_schema(item, host.semantics.get(item.semantic_key))
    if schema is None:
        return ()
    plan = target_plan(schema, item.modes, require_modes=bool(schema.get("modes")))
    groups = item.context.get("target_groups") or {}
    originals = original_copy_groups(host, schema, item.modes, item.targets, groups)
    snapshots = item.context.get("target_snapshots") or {}
    slots = tuple(ref for group in plan.groups for ref in originals[group.group_id])
    result = []
    targeting_source = stack_targeting_source(host, item)
    source_ref = host._stack_source_ref(item)
    for index, current in enumerate(slots):
        if current == recipient.ref and target_identity_matches_snapshot(host, current, snapshots.get(current, {})):
            continue
        proposed = [{"retain": offset} for offset in range(len(slots))]
        proposed[index] = {"ref": recipient.ref}
        try:
            assignment = prepare_copy_targets(
                host, actor=item.controller, schema=schema, modes=item.modes, source_ref=source_ref,
                original_targets=item.targets, original_groups=originals, original_snapshots=snapshots,
                submitted=proposed, acting_source=targeting_source,
                record_optimization=False,
            )
        except GameRuleError:
            continue
        result.append((index, assignment))
    return tuple(result)


def materialize_source_directed_redirect(host, effect, response, *, actor, stack_item):
    from ..rules.source_directed_redirect_shapes import source_directed_redirect_program_is_closed

    try:
        source_directed_redirect_from_effect(effect, resolved=True, continuation="_redirect_slots" in effect)
        if (stack_item is None or stack_item.controller != actor
                or stack_item.kind not in ("triggered_ability", "activated_ability")):
            raise ValueError("The redirect requires its original resolving ability")
        program = host.semantics.get(stack_item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not source_directed_redirect_program_is_closed(program)):
            raise ValueError("The redirect requires its trusted source ability")
        source = ContinuousObjectIdentity(stack_item.source_object_id, stack_item.context.get("source_logical_object_id"))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    target_ref, recipient_ref = effect["stack"], effect["recipient"]
    if target_ref is None or recipient_ref is None:
        return {}, {}
    if target_ref == stack_item.ref:
        raise GameRuleError("An ability cannot redirect its own target")
    recipient = host.state.cards.get(source.object_id)
    if recipient is None or recipient.logical_object_id != source.logical_object_id:
        return {}, {}
    if recipient.ref != recipient_ref:
        raise GameRuleError("The redirect recipient must be its original source")
    options = source_directed_redirect_options(host, target_ref=target_ref, source=source)
    if not options:
        return {}, {}
    target = next(item for item in host.state.stack if item.ref == target_ref)
    schema = host._stack_target_schema(target, host.semantics.get(target.semantic_key))
    key = f"{target.controller}:{target.ref}"
    by_id = {f"slot:{index}": assignment for index, assignment in options}
    bundle = {key: {"authoritative": dict(schema), "redirect_slots": list(by_id)}}
    selected = (response or {}).get("choice")
    if type(selected) is not str or selected not in by_id:
        return bundle, {}
    assignment = by_id[selected]
    return bundle, {key: {
        "targets": list(assignment.targets), "groups": dict(assignment.groups), "snapshots": dict(assignment.snapshots),
    }}
