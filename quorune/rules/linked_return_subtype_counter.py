from __future__ import annotations

from collections.abc import Mapping

from ..characteristic_evaluation import type_parts
from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import resolution_effect_source
from ..counter_placement import CounterPlacementError, place_counters_on_refs
from ..errors import GameRuleError
from ..linked_exile_return_model import (
    ExiledObjectIdentity, LINKED_EXILE_RETURN_CAPABILITY, LINKED_EXILE_RETURN_MECHANIC,
    LINKED_EXILE_RETURN_OPERATION, LINKED_RETURN_OPERATION,
    LINKED_RETURN_SUBTYPE_COUNTER_PHASE, LINKED_RETURN_SUBTYPE_COUNTER_VERSION,
    LinkedExileReturnSpec, is_linked_behold_hand_return_spec,
)
from ..replacement.immutable import thaw_value


LINKED_RETURN_SUBTYPE_COUNTER_CAPABILITY = "zone.linked_exile_return.post_return_subtype_counter"
_LINKED_SUBTYPE_COUNTER_BINDING = "blink:post-return-spirit-counter"
_COUNTER_DEPENDENCIES = (
    LINKED_EXILE_RETURN_CAPABILITY, LINKED_RETURN_SUBTYPE_COUNTER_CAPABILITY,
    "target.revalidate_resolution", "counter.producer.fixed_effect", "counter.placement.quantity_replacement",
)


def linked_subtype_counter_target():
    from ..compiler.direct_target import DirectPermanentTargetSpec

    return DirectPermanentTargetSpec(types_any=("creature",), controller_relation="you").to_target_schema()


def linked_subtype_counter_effects():
    spec = LinkedExileReturnSpec(schema_version=LINKED_RETURN_SUBTYPE_COUNTER_VERSION)
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": _LINKED_SUBTYPE_COUNTER_BINDING}
    return (
        {**common, "phase": "exile", "spec": spec.to_dict(), "cards": "$targets"},
        {**common, "phase": "return", "spec": spec.to_dict()},
        {**common, "phase": LINKED_RETURN_SUBTYPE_COUNTER_PHASE, "spec": spec.to_dict()},
    )


def uses_linked_subtype_counter(effect):
    if not isinstance(effect, Mapping) or effect.get("op") not in {LINKED_EXILE_RETURN_OPERATION, LINKED_RETURN_OPERATION}:
        return False
    spec = effect.get("spec")
    return (
        effect.get("binding_id") == _LINKED_SUBTYPE_COUNTER_BINDING
        or effect.get("phase") == LINKED_RETURN_SUBTYPE_COUNTER_PHASE
        or (isinstance(spec, Mapping) and spec.get("schema_version") == LINKED_RETURN_SUBTYPE_COUNTER_VERSION
            and not is_linked_behold_hand_return_spec(spec))
    )


def linked_subtype_counter_node_capabilities(effects, target_schema, mechanics):
    if (thaw_value(effects) != list(linked_subtype_counter_effects())
            or thaw_value(target_schema) != linked_subtype_counter_target()
            or not {LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets"}.issubset(mechanics)):
        return ()
    try:
        for effect in effects:
            LinkedExileReturnSpec.from_dict(effect["spec"])
    except (TypeError, ValueError):
        return ()
    return _COUNTER_DEPENDENCIES


def linked_subtype_counter_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_linked_subtype_counter(effect) for effect in nested_effects(program.effects)):
        return ()
    required = linked_subtype_counter_node_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers):
        raise ValueError("Post-return counters require the complete fixed spell and target")
    return required


def require_linked_subtype_counter_source(host, item, effect, actor):
    program = host.semantics.get(item.semantic_key)
    targets = tuple(ref for ref in item.targets if ref is not None)
    if (
        item.kind not in {"spell", "spell_copy"} or item.controller != actor
        or item.context.get("currently_resolving") is not True or len(targets) != 1
        or program is None or not host.semantic_program_is_current_trusted(program)
        or LINKED_RETURN_SUBTYPE_COUNTER_CAPABILITY not in program.capability_dependencies
        or effect["binding_id"] != _LINKED_SUBTYPE_COUNTER_BINDING
        or effect["phase"] == "exile" and tuple(effect["cards"] or ()) != targets
    ):
        raise GameRuleError("Post-return counters require their original resolving target")


def record_linked_subtype_counter_arrival(host, binding, arrival_refs):
    if len(binding["objects"]) > 1 or len(arrival_refs) > len(binding["objects"]):
        raise GameRuleError("Post-return counters require at most one linked arrival")
    arrival = None
    if arrival_refs:
        exiled = ExiledObjectIdentity.from_dict(binding["objects"][0])
        card = host.state.cards.get(exiled.object_id)
        if (card is None or card.ref != arrival_refs[0] or card.zone != "battlefield"
                or card.logical_object_id == exiled.logical_object_id):
            raise GameRuleError("Post-return counter subject changed during entry")
        arrival = ContinuousObjectIdentity(card.object_id, card.logical_object_id).to_dict()
    binding["returned"] = arrival
    binding["counter_completed"] = False


def commit_linked_subtype_counter(host, item, effect, binding, *, actor, reason):
    if (not isinstance(binding, Mapping) or set(binding) != {
            "spec", "objects", "completed", "returned", "counter_completed",
        } or binding["completed"] is not True or binding["counter_completed"] is not False
            or binding["spec"] != thaw_value(effect["spec"])
            or not isinstance(binding["objects"], (list, tuple))
            or len(binding["objects"]) > 1
            or binding["returned"] is not None and not binding["objects"]):
        raise GameRuleError("Post-return counter lost its completed linked return")
    results = []
    if binding["returned"] is not None:
        identity = ContinuousObjectIdentity.from_dict(binding["returned"])
        exiled = ExiledObjectIdentity.from_dict(binding["objects"][0])
        if identity.object_id != exiled.object_id or identity.logical_object_id == exiled.logical_object_id:
            raise GameRuleError("Post-return counter identity does not belong to its linked return")
        card = host.state.cards.get(identity.object_id)
        if (card is not None and card.zone == "battlefield" and not card.phased_out
                and card.logical_object_id == identity.logical_object_id):
            _, subtypes, _ = type_parts(str(host._effective_card_data(card).get("type_line") or ""))
            if "spirit" in subtypes:
                source = resolution_effect_source(host, effect)
                try:
                    place_counters_on_refs(
                        host, actor=actor, object_refs=(card.ref,), counter_name="+1/+1", amount=1,
                        source_ref=source.card_ref or item.ref, reason=reason,
                        selections=tuple(effect.get("_replacement_selections") or ()),
                    )
                except CounterPlacementError as exc:
                    raise GameRuleError(str(exc)) from exc
                results.append(card.ref)
    binding["counter_completed"] = True
    return results
