from __future__ import annotations

"""Linked lifecycle coordination over canonical zone, entry and trigger owners."""

from typing import Any, Mapping, Protocol, Sequence

from .continuous_effects import ContinuousOperation, Layer
from .continuous_effect_state import (
    create_resolution_continuous_effect,
    resolution_effect_source,
)
from .entry_counter_model import EffectEntryCounter
from .errors import GameRuleError
from .linked_exile_return_model import LinkedExileReturnSpec, LINKED_RETURN_OPERATION
from .trigger_processing import schedule_delayed_trigger
from .zone_transitions import ZoneTransitionOwner


class LinkedExileReturnHost(Protocol):
    state: Any
    active_seats: Sequence[str]

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> Any: ...


def _spec(effect: Mapping[str, Any]) -> LinkedExileReturnSpec:
    try:
        return LinkedExileReturnSpec.from_dict(effect.get("spec"))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def _source_item(host: LinkedExileReturnHost, effect: Mapping[str, Any]) -> Any:
    source = effect.get("_runtime_source")
    if not isinstance(source, Mapping) or type(source.get("stack_ref")) is not str:
        raise GameRuleError("Linked exile/return requires authoritative stack context")
    item = next((item for item in host.state.stack if item.ref == source["stack_ref"]), None)
    if item is None:
        raise GameRuleError("Linked exile/return source stack object is unavailable")
    return item


def _eligible_exiled(host: LinkedExileReturnHost, objects: Any) -> tuple[Any, ...]:
    if not isinstance(objects, (list, tuple)):
        raise GameRuleError("Linked exile identities must be an array")
    result = []
    seen = set()
    for identity in objects:
        if (
            not isinstance(identity, Mapping)
            or set(identity) != {"object_id", "ref", "logical_object_id"}
            or any(type(value) is not str or not value for value in identity.values())
        ):
            raise GameRuleError("Linked exile identity is malformed")
        if identity["object_id"] in seen:
            raise GameRuleError("Linked exile identities must be unique")
        seen.add(identity["object_id"])
        card = host.state.cards.get(identity["object_id"])
        if (
            card is not None
            and card.ref == identity["ref"]
            and card.logical_object_id == identity["logical_object_id"]
            and card.zone == "exile"
            and not card.is_token
            and not card.is_spell_copy
        ):
            result.append(card)
    return tuple(result)


def return_linked_exiled_objects(
    host: LinkedExileReturnHost,
    effect: Mapping[str, Any],
    *, actor: str, operation: str, reason: str,
) -> list[str]:
    del operation
    allowed = {"op", "objects", "spec", "_runtime_source", "_replacement_selections"}
    if set(effect) - allowed or not {"op", "objects", "spec"}.issubset(effect):
        raise GameRuleError("Linked return has an invalid shape")
    spec = _spec(effect)
    cards = _eligible_exiled(host, effect["objects"])
    if not cards:
        return []
    source = resolution_effect_source(host, effect)
    counters = {
        card.object_id: tuple(
            EffectEntryCounter(
                name, amount, actor, source.card_ref or source.stack_ref, "400.7j"
            )
            for name, amount in spec.entry_counters
        )
        for card in cards
    }
    controllers = {
        card.object_id: actor if spec.controller == "actor" else card.owner
        for card in cards
    }
    if any(controller not in host.active_seats for controller in controllers.values()):
        raise GameRuleError("Linked return controller is unavailable")
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason,
        tapped=spec.tapped, destination_controllers=controllers,
        effect_entry_counters=counters,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    )
    returned = tuple(card for card in moved if card.zone == "battlefield")
    if spec.keywords and returned:
        create_resolution_continuous_effect(
            host, source=source, targets=returned,
            layer=Layer.ABILITY, sublayer="6",
            operations=tuple(
                ContinuousOperation("add_ability", keyword)
                for keyword in spec.keywords
            ),
        )
    return [card.ref for card in returned]


def resolve_linked_exile_return(
    host: LinkedExileReturnHost,
    effect: Mapping[str, Any],
    *, actor: str, operation: str, reason: str,
) -> list[str]:
    del operation
    phase = effect.get("phase")
    required = {"op", "phase", "binding_id", "spec", *(("cards",) if phase == "exile" else ())}
    if phase not in {"exile", "return"} or set(effect) - (required | {"_runtime_source", "_replacement_selections"}) or not required.issubset(effect):
        raise GameRuleError("Linked exile/return instruction is malformed")
    binding_id = effect["binding_id"]
    if type(binding_id) is not str or not binding_id.startswith("blink:"):
        raise GameRuleError("Linked exile/return binding is malformed")
    spec = _spec(effect)
    item = _source_item(host, effect)
    journal = item.context.setdefault("linked_exile_returns", {})
    if not isinstance(journal, dict):
        raise GameRuleError("Linked exile/return continuation is malformed")
    if phase == "exile":
        if binding_id in journal:
            raise GameRuleError("Linked exile instruction has already committed")
        refs = effect["cards"]
        if refs is None:
            refs = []
        if isinstance(refs, str):
            refs = [refs]
        if not isinstance(refs, (list, tuple)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
            raise GameRuleError("Linked exile targets must be unique references")
        cards = tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)
        if any(card.phased_out for card in cards):
            raise GameRuleError("Linked exile target is phased out")
        moved = (
            ZoneTransitionOwner(host).move_cards_simultaneously(
                tuple((card.object_id, "exile") for card in cards), reason=reason,
                replacement_selections=tuple(effect.get("_replacement_selections") or ()),
            )
            if cards else ()
        )
        objects = [{"object_id": card.object_id, "ref": card.ref, "logical_object_id": card.logical_object_id} for card in moved if card.zone == "exile" and not card.is_token and not card.is_spell_copy]
        journal[binding_id] = {"spec": spec.to_dict(), "objects": objects, "completed": False}
        return [card.ref for card in moved]
    binding = journal.get(binding_id)
    if not isinstance(binding, Mapping) or set(binding) != {"spec", "objects", "completed"} or binding["spec"] != spec.to_dict() or binding["completed"] is not False:
        raise GameRuleError("Linked return continuation changed or already completed")
    objects = binding["objects"]
    if spec.timing == "immediate":
        result = return_linked_exiled_objects(
            host,
            {"op": LINKED_RETURN_OPERATION, "objects": objects,
             "spec": spec.to_dict(), "_runtime_source": effect["_runtime_source"],
             "_replacement_selections": effect.get("_replacement_selections", ())},
            actor=actor, operation=LINKED_RETURN_OPERATION, reason=reason,
        )
    else:
        result = []
        if objects:
            condition = {"step": "end_step"}
            if spec.timing == "controller_next_end_step":
                condition["player"] = actor
            source = effect["_runtime_source"]
            schedule_delayed_trigger(
                host, controller=actor, label="Return linked exiled objects",
                event_kind="step.begin", condition=condition,
                stack_template={"context": {"dynamic_effects": [
                    {"op": LINKED_RETURN_OPERATION, "objects": objects,
                     "spec": spec.to_dict()}
                ]}},
                source_object_id=source.get("object_id"),
                referred_object_ids=tuple(identity["object_id"] for identity in objects),
                once=True,
            )
    binding["completed"] = True
    return result
