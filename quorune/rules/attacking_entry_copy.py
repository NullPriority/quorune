"""One copy of a different attacking creature from a hand-entry trigger."""

from ..object_predicate import PermanentStatePredicateSpec
from ..replacement.immutable import thaw_value
from .attacking_token_results import ATTACKING_TOKEN_CAPABILITY, ATTACKING_TOKEN_OPERATION


CAPABILITY = "token.creation.attacking_entry_target_copy"
ENTRY_FACT = "entered_from_hand_attacking"
VERSION = 4
DEPENDENCIES = (
    CAPABILITY, ATTACKING_TOKEN_CAPABILITY, "combat.entry.lifecycle.fixed_public",
    "trigger.event.normalized_zone_change", "trigger.effect.fixed_event", "trigger.placement.apnap",
    "target.revalidate_resolution", "token.creation.fixed_copy",
)


def condition():
    return {"all": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": "from", "op": "eq", "value": "hand"},
        {"field": "owner", "op": "eq", "value": "$source.controller"},
        {"field": ENTRY_FACT, "op": "eq", "value": True},
    ]}


def target_schema():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_all": ["creature"], "count": 1,
        "another": True, "state_predicate": PermanentStatePredicateSpec(attacking=True).to_dict(),
    }


def effect():
    return {"op": ATTACKING_TOKEN_OPERATION, "player": "$controller", "quantity": 1,
            "tapped": True, "copy_of": "$target.0", "token_entry_version": VERSION}


def uses_copy(effect):
    return (effect.get("op") == ATTACKING_TOKEN_OPERATION
            and (effect.get("token_entry_version") == VERSION or "copy_of" in effect))


def validate_compiled(effect_value):
    if type(effect_value.get("token_entry_version")) is not int or thaw_value(effect_value) != effect():
        raise ValueError("Attacking entry requires its exact tapped copy of a different attacker")


def runtime_dependencies(program):
    if not any(uses_copy(row) for row in program.effects):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter", "battlefield")
            or len(program.effects) != 1 or thaw_value(program.target_schema) != target_schema()
            or thaw_value(program.event_condition) != condition()):
        raise ValueError("Attacking entry copy requires its exact hand arrival and a different attacker")
    validate_compiled(program.effects[0])
    return DEPENDENCIES


def current_entry_copy_subscriber(host):
    from .event_subscriptions import event_has_capability_subscriber

    return event_has_capability_subscriber(host, "permanent.enter", CAPABILITY)


def capture_entry_fact(host, context, card, occurrence):
    if (occurrence.origin != "hand" or occurrence.owner != occurrence.current_controller
            or not card.attacking or card.phased_out or not current_entry_copy_subscriber(host)):
        return context
    from ..entry_copy import copiable_oracle_id

    programs = host.semantics.programs_for_oracle(copiable_oracle_id(card), event="permanent.enter")
    if not any(CAPABILITY in row.capability_dependencies and host.semantic_program_is_current_trusted(row) for row in programs):
        return context
    return {**dict(context), ENTRY_FACT: True}


def materialize_copy(host, item, raw, bound):
    validate_compiled(raw)
    if item.context.get("from") != "hand" or item.context.get(ENTRY_FACT) is not True:
        raise ValueError("Attacking entry copy lost its original committed entry")
    source = host.state.cards.get(item.source_object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out or not source.attacking
            or source.logical_object_id != item.context.get("source_logical_object_id")):
        return None
    target = next((card for card in host.state.cards.values() if card.ref == bound.get("copy_of")), None)
    if (target is None or target.object_id == source.object_id or target.zone != "battlefield"
            or target.phased_out or not target.attacking):
        return None
    from ..token_creation import token_copy_snapshot

    return {**bound, "copy_snapshot": token_copy_snapshot(host, target)}


def validate_materialized(value, actor):
    raw = {key: item for key, item in value.items() if key != "copy_snapshot"}
    if raw.get("player") != actor or type(raw.get("copy_of")) is not str or not raw["copy_of"]:
        raise ValueError("Attacking entry copy requires its controller and copied attacker")
    validate_compiled({**raw, "player": "$controller", "copy_of": "$target.0"})
    snapshot = value.get("copy_snapshot")
    from collections.abc import Mapping

    if not isinstance(snapshot, Mapping) or set(snapshot) != {"oracle_id", "printed_name", "annotations", "characteristics"}:
        raise ValueError("Attacking entry copy requires the original copiable snapshot")
