from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any, Mapping, Protocol

from .linked_ability_exile_model import LINKED_ABILITY_EXILE_ANNOTATION, LinkedAbilityExile
from .mandatory_loop_model import MandatoryLinkedLoopWatch
from .public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from .rules.linked_exile_return_shapes import linked_ability_exile_runtime_dependencies
from .target_source import stack_targeting_source
from .targets import TargetGroup
from .util import stable_json


_PRIORITY_EVENTS = frozenset({"activate", "resolve", "mana.activate"})
_PASSIVE_COMBAT_CAPABILITIES = frozenset({
    "combat.attack.vigilance", "combat.block.flying", "combat.block.landwalk.basic_type", "combat.block.shadow",
})
_HISTORY_AVAILABILITY_CAPABILITIES = frozenset({PUBLIC_HISTORY_QUERY_CAPABILITY, "activation.condition.fixed_public_state"})


class TriggerTargetingHost(Protocol):
    """Narrow engine surface needed to place triggered-ability targets."""

    state: Any
    semantics: Any
    permissions: Any

    def _stack_target_schema(
        self,
        item: Any,
        program: Any,
    ) -> Mapping[str, Any] | None: ...

    def _public_target_schema(
        self,
        controller: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str,
        acting_source: Any = None,
    ) -> Mapping[str, Any] | None: ...

    def _stack_source_ref(self, item: Any) -> str: ...

    def _target_selection_continuation(
        self,
        *,
        actor: str,
        item: Any,
        public_schema: Mapping[str, Any],
        trigger_creation: bool = False,
    ) -> Any: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


def _reset_linked_loop(state):
    if state.mandatory_linked_loop is not None and state.mandatory_linked_loop.cursor is not None:
        state.mandatory_linked_loop = MandatoryLinkedLoopWatch()


def record_linked_loop_decision(state, decision):
    """Discard the witness on any completed choice outside its forced path."""
    watch = getattr(state, "mandatory_linked_loop", None)
    if watch is None or watch.cursor is None:
        return
    if decision.kind == "priority" and all(value.get("action") == "pass" for value in decision.responses.values()):
        return
    if decision.kind == "semantic.target":
        selection = decision.continuation.get("selection")
        if isinstance(selection, Mapping) and selection.get("stack_ref") == watch.cursor:
            return
    _reset_linked_loop(state)


def _is_closed_link_pair(host, programs):
    if len(programs) != 2 or {program.event for program in programs} != {"permanent.enter.self", "permanent.leave.self"}:
        return False
    pairs = set()
    for program in programs:
        if not linked_ability_exile_runtime_dependencies(program) or not host.semantic_program_is_current_trusted(program):
            return False
        effect = program.effects[0]
        if effect["op"] == "offer_optional_effect":
            effect = effect["effects"][0]
        pairs.add(effect["binding_id"])
    return len(pairs) == 1


def _is_passive_combat_program(host, program):
    return (program.event == "continuous" and not program.effects and not program.handlers
            and len(program.capability_dependencies) == 1
            and program.capability_dependencies[0] in _PASSIVE_COMBAT_CAPABILITIES
            and host.semantic_program_is_current_trusted(program))


def _loop_participants(host):
    """Admit only a closed set of automatic effects at this boundary."""
    if host.state.continuous_effects or host.state.annotations or any(trigger.active for trigger in host.state.delayed_triggers):
        return None
    sources = host._semantic_event_sources()
    programs = {card.oracle_id: [] for card in sources}
    for key in host.semantics.keys():
        program = host.semantics.get(key)
        if program.oracle_id in programs:
            programs[program.oracle_id].append(program)
    participants = {}
    for card in sources:
        if card.zone == "battlefield" and (
            card.face_down or card.phased_out or card.object_kind != "card" or card.permanent_composition is not None
            or card.attached_to or card.attachments or set(card.annotations) - {LINKED_ABILITY_EXILE_ANNOTATION}
        ):
            return None
        automatic = []
        for program in programs[card.oracle_id]:
            if program.event in _PRIORITY_EVENTS:
                closure = program.capability_closure or {}
                if _HISTORY_AVAILABILITY_CAPABILITIES.intersection(
                    (*program.capability_dependencies, *closure.get("reachable", ()))
                ):
                    return None
            elif not _is_passive_combat_program(host, program):
                automatic.append(program)
        battlefield = [program for program in automatic if program.active_zone == "battlefield"]
        if card.zone in {"battlefield", "exile"} and _is_closed_link_pair(host, battlefield):
            if not card.is_card_object or card.is_commander or card.source_continuity is not None:
                return None
            participants[card.object_id] = card
        elif card.zone == "battlefield" and battlefield:
            return None
        if card.zone != "battlefield" and any(program.active_zone == card.zone for program in automatic):
            return None
    return participants


def _loop_card_links(card, records, participants):
    if not isinstance(records, list):
        raise ValueError("Linked loop requires a closed link record list")
    result = []
    for value in records:
        link = LinkedAbilityExile.from_dict(value)
        if link.exiled.object_id != card.object_id or link.exiled.ref != card.ref:
            raise ValueError("A link belongs to another object")
        data = link.to_dict()
        source = participants.get(link.source.object_id)
        if source is not None and link.source.logical_object_id == source.logical_object_id:
            data["source"]["logical_object_id"] = None
        if link.exiled.logical_object_id == card.logical_object_id:
            data["exiled"]["logical_object_id"] = None
        result.append(data)
    return result


def _linked_loop_position(state, item, schema, participants):
    """Compare a closed loop position separately from exact replay identity."""
    payload = deepcopy(state.to_dict(include_ephemeral=False))
    for field in ("mandatory_linked_loop", "revision", "event_sequence", "priority_epoch", "ref_counters",
                  "timestamp_sequence", "action_opportunities", "opportunity_sequence"):
        payload.pop(field)
    for player in payload["players"].values():
        player["stats"].pop("decision_optimization", None)
    for object_id, card in participants.items():
        data = payload["cards"][object_id]
        for field in ("zone_change_counter", "zone_timestamp", "acquired_control_timestamp"):
            data.pop(field, None)
        if LINKED_ABILITY_EXILE_ANNOTATION in data["annotations"]:
            records = data["annotations"][LINKED_ABILITY_EXILE_ANNOTATION]
            data["annotations"][LINKED_ABILITY_EXILE_ANNOTATION] = _loop_card_links(card, records, participants)
    if history := payload.get("turn_history"):
        # The closed effects above cannot read the recurring arrival, departure
        # or targeting-crime facts. Keep their actual journal in the checkpoint.
        history["events"] = [event for event in history["events"]
            if event["kind"] != "crime_committed" and (
                event["kind"] not in {"permanent_entered", "permanent_left", "card_arrived_exile"}
                or (event.get("object_incarnation") or "").rpartition("@")[0] not in participants
            )]
    stack = payload["stack"][0]
    del stack["ref"], stack["stack_id"]
    context = stack["context"]
    source = participants[item.source_object_id]
    context["source_logical_object_id"] = None
    if context.get("card_object_identity") == source.logical_object_id:
        context["card_object_identity"] = None
    if context.get("card_zone_change_counter") == source.zone_change_counter:
        context["card_zone_change_counter"] = None
    targeting = context.get("targeting_source_characteristics")
    if isinstance(targeting, dict) and targeting.get("logical_object_id") == source.logical_object_id:
        targeting["logical_object_id"] = None
    payload["target"] = schema
    return sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _observe_linked_loop(host, item, program, schema):
    state = host.state
    watch = getattr(state, "mandatory_linked_loop", None)
    if watch is None:
        return False
    if (schema is None or len(state.stack) != 1 or state.pending_trigger_batches or program is None
            or program.event != "permanent.enter.self" or len(program.effects) != 1
            or program.effects[0].get("op") != "linked_exile_return" or item.context.get("dynamic_effects")):
        _reset_linked_loop(state)
        return False
    try:
        required = linked_ability_exile_runtime_dependencies(program)
        group = TargetGroup.from_mapping(program.target_schema)
        if not required or (group.min_targets, group.max_targets) != (1, 1) or len(schema.get("legal_refs", ())) != 1:
            _reset_linked_loop(state)
            return False
        participants = _loop_participants(host)
        source = participants.get(item.source_object_id) if participants is not None else None
        if (source is None or source.zone != "battlefield"
                or item.context.get("source_logical_object_id") != source.logical_object_id
                or not any(card.zone == "battlefield" and card.ref == schema["legal_refs"][0] for card in participants.values())):
            _reset_linked_loop(state)
            return False
        signature = _linked_loop_position(state, item, schema, participants)
    except (ValueError, TypeError, KeyError):
        _reset_linked_loop(state)
        return False
    state.mandatory_linked_loop, repeated = watch.observe(signature, item.ref)
    if repeated:
        from .state_based_execution import commit_game_draw
        commit_game_draw(host, reason="Mandatory linked abilities repeated a forced cycle.")
    return repeated


def begin_pending_trigger_target_selection(
    host: TriggerTargetingHost,
    *,
    decision_role: str,
    log_reason_field: str,
) -> bool:
    """Choose CR 603.3d targets as a represented trigger enters the stack.

    Dynamic typed triggers and registry-backed semantic triggers share this
    boundary.  The caller supplies only target/schema services; this module
    owns removal when mandatory targets have disappeared and issuance of the
    seat-scoped target capability.
    """

    while True:
        removed_invalid_trigger = False
        for item in host.state.stack:
            if not item.context.get("trigger_target_selection_pending"):
                continue
            program = host.semantics.get(item.semantic_key)
            target_schema = host._stack_target_schema(item, program)
            if not target_schema:
                item.context.pop("trigger_target_selection_pending", None)
                continue
            public_schema = host._public_target_schema(
                item.controller,
                target_schema,
                acting_source=stack_targeting_source(host,item), source_ref=host._stack_source_ref(item),
            )
            if _observe_linked_loop(host, item, program, public_schema):
                return True
            if public_schema is None:
                host.state.stack.remove(item)
                host._log(
                    item.controller,
                    "stack.trigger.removed",
                    (
                        f"Removed {item.ref}: {item.label}; its mandatory "
                        "targets could not be chosen."
                    ),
                    {
                        "stack": item.ref,
                        log_reason_field: "no_legal_targets",
                    },
                    importance=2,
                )
                removed_invalid_trigger = True
                break
            host.permissions.issue(
                kind="semantic.target",
                role=decision_role,
                actors=[item.controller],
                allowed_actions=["choose"],
                payload_by_actor={
                    item.controller: {
                        "stack": item.ref,
                        "prompt": f"Choose legal targets for {item.label}.",
                        "target_schema": public_schema,
                        "legal_actions": [
                            {
                                "id": "choose",
                                "action": "choose",
                                "target_schema": public_schema,
                            }
                        ],
                    }
                },
                continuation={
                    "selection": host._target_selection_continuation(
                        actor=item.controller,
                        item=item,
                        public_schema=public_schema,
                        trigger_creation=True,
                    ).to_dict()
                },
            )
            return True
        if not removed_invalid_trigger:
            return False


__all__ = ["begin_pending_trigger_target_selection"]
