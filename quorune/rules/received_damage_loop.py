"""A closed rules proof for reciprocal receiving-damage watches."""

from hashlib import sha256

from ..event_damage_source_model import EVENT_DAMAGE_SOURCE_CONTEXT, EventDamageSourceInformation
from ..received_damage_model import RECEIVED_DAMAGE_EVENT, RECEIVED_DAMAGE_WATCH_FIELD, ReceivedDamageWatch
from ..replacement.immutable import thaw_value
from ..semantic_runtime.damage_replacements import DamageReplacementCondition, FixedDamagePreventionHandler
from ..util import stable_json
from .received_damage_watch import watched_damage_result


_INDESTRUCTIBLE = "permanent.indestructible.ordinary"
_STATIC_PREVENTION = "damage.prevention.static_fixed"
_PLAYER_PREVENTION = DamageReplacementCondition("any", "source_controller", ("player",), (), (), None)
_ACTION_GROUPS = ("cast", "lands", "abilities", "mana_abilities", "special_actions")


def _watch_pair(state, item):
    active = [trigger for trigger in state.delayed_triggers if trigger.active]
    if len(active) != 2 or any(RECEIVED_DAMAGE_WATCH_FIELD not in trigger.stack_template for trigger in active):
        return None
    watches = [(trigger, ReceivedDamageWatch.from_trigger(trigger)) for trigger in active]
    if any(trigger.expires_turn_sequence != state.turn_sequence for trigger, _ in watches):
        return None
    if len({watch.object_id for _, watch in watches}) != 2:
        return None
    current = next(((trigger, watch) for trigger, watch in watches
                    if trigger.ref == item.context.get("delayed_trigger_ref")), None)
    if current is None:
        return None
    trigger, watch = current
    if (
        item.kind != "triggered_ability" or item.semantic_key is not None or item.targets
        or item.controller != trigger.controller or item.source_object_id != trigger.source_object_id
        or item.context.get("event") != RECEIVED_DAMAGE_EVENT
        or item.context.get("received_damage_watch_source_stack") != watch.source_stack_ref
        or any(item.context.get(key) != value for key, value in watch.condition.items())
    ):
        return None
    info = EventDamageSourceInformation.from_dict(item.context[EVENT_DAMAGE_SOURCE_CONTEXT])
    if (
        info.event != RECEIVED_DAMAGE_EVENT or info.event_id != item.context.get("event_id")
        or info.source_snapshot.logical_object_id != watch.logical_object_id
        or type(item.context.get("amount")) is not int or info.received_amount != item.context["amount"]
        or item.context.get("dynamic_effects") != [watched_damage_result(info.received_amount)]
    ):
        return None
    return {instance.object_id: instance.logical_object_id for _, instance in watches}, info.received_amount


def _closed_program(host, card):
    model = host.semantics.card_program_for_oracle(card.oracle_id)
    return model is not None and model.trust_closure.get("strict_capability_ready") is True and not model.residuals


def _plain_permanent(card):
    return not (
        card.face_down or card.phased_out or not card.is_card_object or card.is_commander
        or card.permanent_composition is not None or card.source_continuity is not None
        or card.attached_to or card.attachments or card.annotations or card.counters
    )


def _board_program(host, card):
    programs = host.semantics.programs_for_oracle(card.oracle_id)
    if len(programs) != 1 or not _closed_program(host, card):
        return None
    program, = programs
    if (
        program.active_zone != "battlefield" or program.effects or program.cost_schema is not None
        or program.event_condition is not None or not host.semantic_program_is_current_trusted(program)
    ):
        return None
    return program


def _has_persistent_player_prevention(program, amount):
    if (
        program.event != "damage" or len(program.handlers) != 1
        or tuple(program.capability_dependencies) != (_STATIC_PREVENTION,)
    ):
        return False
    handler, = program.handlers
    node = FixedDamagePreventionHandler().validate(handler)
    return node.condition == _PLAYER_PREVENTION and node.amount >= amount


def _closed_board(host, receivers, amount):
    shields = []
    present = set()
    for card in host.state.cards.values():
        if card.zone != "battlefield":
            continue
        if card.controller not in host.active_seats or not _plain_permanent(card):
            return False
        program = _board_program(host, card)
        if program is None:
            return False
        data = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(data["type_line"]))
        keywords = {keyword.casefold() for keyword in data.get("keywords", ())}
        if card.object_id in receivers:
            if (
                card.logical_object_id != receivers[card.object_id] or "creature" not in types
                or not types <= {"creature", "artifact"} or int(data["toughness"]) <= 0
                or keywords != {"indestructible"} or program.event != "continuous"
                or program.handlers or tuple(program.capability_dependencies) != (_INDESTRUCTIBLE,)
            ):
                return False
            present.add(card.object_id)
        else:
            if types != {"artifact"} or keywords or not _has_persistent_player_prevention(program, amount):
                return False
            shields.append(card.controller)
    return present == set(receivers) and sorted(shields) == sorted(host.active_seats)


def _closed_priority(host):
    state = host.state
    # Use public hand and mana facts as the cheap gate before consulting action offers.
    if any(state.players[seat].zones["hand"] or any(state.players[seat].mana_pool.values())
           for seat in host.active_seats):
        return False
    for card in state.cards.values():
        if card.zone in {"battlefield", "library"}:
            continue
        if card.zone not in {"graveyard", "exile", "command"} or card.face_down or not _closed_program(host, card):
            return False
        if any(program.active_zone in {card.zone, "all"}
               for program in host.semantics.programs_for_oracle(card.oracle_id)):
            return False
    for seat in host.active_seats:
        hints = host._priority_action_hints(seat)
        if any(hints.get(key) for key in _ACTION_GROUPS):
            return False
        if any(action.get("id") not in {"pass", "concede"} for action in hints["actions"]):
            return False
    return True


def _position_signature(state, receivers):
    payload = state.to_dict(include_ephemeral=False)
    for field in (
        "mandatory_linked_loop", "revision", "event_sequence", "priority_epoch", "ref_counters",
        "timestamp_sequence", "action_opportunities", "opportunity_sequence",
    ):
        payload.pop(field)
    for player in payload["players"].values():
        player["stats"].pop("decision_optimization", None)
    for object_id in receivers:
        # Exact passive indestructibility and the closed decision surface make
        # these ever-growing marks unobservable to the recurring rules.
        payload["cards"][object_id].pop("marked_damage")
    incarnations = set(receivers.values())
    history = payload["turn_history"]
    history["events"] = [event for event in history["events"] if not (
        event["kind"] == "permanent_damaged" and event["object_incarnation"] in incarnations
        and event["target_object_incarnation"] in incarnations
    )]
    frame, = payload["stack"]
    del frame["ref"], frame["stack_id"]
    del frame["context"]["event_id"], frame["context"][EVENT_DAMAGE_SOURCE_CONTEXT]["event_id"]
    return sha256(stable_json(payload).encode("utf-8")).hexdigest()


def received_damage_loop_position(host):
    """Prove that neither half of this specific reciprocal sequence can progress."""
    state = host.state
    if (
        state.game_over or state.pending_decision is not None or state.pending_trigger_batches
        or len(state.stack) != 1 or state.priority_passes or state.priority_player != state.active_player
        or (state.phase, state.step) not in {("precombat_main", "main"), ("postcombat_main", "main")}
        or state.config.effective_profile(len(state.players)) not in {"commander_duel", "commander_multiplayer"}
        or state.continuous_effects or state.damage_prevention_shields or state.damage_redirections or state.annotations
        or state.turn_history is None or state.turn_history.turn_sequence != state.turn_sequence
    ):
        return None
    item, = state.stack
    if "received_damage_watch_source_stack" not in item.context or EVENT_DAMAGE_SOURCE_CONTEXT not in item.context:
        return None
    pair = _watch_pair(state, item)
    if pair is None:
        return None
    receivers, amount = pair
    if not _closed_board(host, receivers, amount) or not _closed_priority(host):
        return None
    return _position_signature(state, receivers), item.ref
