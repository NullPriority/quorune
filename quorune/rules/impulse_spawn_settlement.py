"""Committed impulse incarnations and their once-only end-step settlement."""
from collections.abc import Mapping

from ..impulse_access_model import POWER_COUNTED_IMPULSE_VERSION, SPAWN_SETTLEMENT_IMPULSE_VERSION, spawn_settlement_impulse_effect
from ..replacement.immutable import FrozenMap, thaw_value

CAPABILITY = "trigger.delayed.linked_impulse_spawn_settlement"
DEPENDENCIES = (
    CAPABILITY, "zone.impulse_access.fixed", "zone.change.destination_replacement",
    "zone.graveyard.ordered_simultaneous_arrival", "token.creation.fixed_definition", "mana.activated.fixed_output",
)
IF_ANY_FIELD = "next_end_step_remaining_impulse"
RECEIPT_FIELD = "spawn_settlement_receipt"
SETTLEMENT_FIELD = "linked_landfall_settlement"


def spawn_program_dependencies(program):
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or program.cost_schema is not None or program.handlers or program.target_schema is not None
            or program.event_condition is not None
            or tuple(thaw_value(program.effects)) != (spawn_settlement_impulse_effect(),)):
        raise ValueError("Spawn settlement requires its exact resolving three-card impulse")
    return DEPENDENCIES


def _current_program(host, semantic_key):
    program = host.semantics.get(semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Spawn settlement requires the original trusted spell")
    spawn_program_dependencies(program)
    return program


def validate_spawn_receipt(value):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "controller", "semantic_key", "objects"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or any(type(value[k]) is not str or not value[k] for k in ("controller", "semantic_key"))
            or not isinstance(value["objects"], (list, tuple)) or not 1 <= len(value["objects"]) <= 3):
        raise ValueError("Spawn settlement requires one closed set of actual exile incarnations")
    refs = set()
    for row in value["objects"]:
        if (not isinstance(row, Mapping) or set(row) != {"object_id", "logical_object_id", "ref"}
                or any(type(part) is not str or not part for part in row.values())
                or not row["logical_object_id"].startswith(row["object_id"] + "@") or row["ref"] in refs):
            raise ValueError("Spawn settlement contains an invalid exile incarnation")
        refs.add(row["ref"])
    return FrozenMap(value)


def live_exile_rows(state, receipt):
    for row in validate_spawn_receipt(receipt)["objects"]:
        card = state.cards.get(row["object_id"])
        if (card is not None and card.zone == "exile" and card.is_card_object
                and card.owner == receipt["controller"] and card.ref == row["ref"]
                and card.logical_object_id == row["logical_object_id"]):
            yield card


def resolve_impulse_intent_with_delayed_settlement(host, intent):
    from ..errors import GameRuleError
    from ..impulse_access import ImpulseAccessRequest, resolve_fixed_impulse_access

    if intent.schema_version == POWER_COUNTED_IMPULSE_VERSION:
        from .power_counted_impulse import resolve_impulse

        try:
            return resolve_impulse(host, intent)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
    linked = intent.schema_version == SPAWN_SETTLEMENT_IMPULSE_VERSION
    item = host.state.stack[-1] if linked and host.state.stack else None
    if linked:
        try:
            if (item is None or item.kind not in {"spell", "spell_copy"}
                    or item.context.get("currently_resolving") is not True or item.controller != intent.actor):
                raise ValueError("Spawn settlement lost its resolving original")
            _current_program(host, item.semantic_key)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
    result = resolve_fixed_impulse_access(host, ImpulseAccessRequest(
        actor=intent.actor, player=intent.player, count=intent.count, duration=intent.duration,
        reason=intent.reason, schema_version=intent.schema_version,
    ))
    if linked and result.exiled_refs:
        schedule_spawn_settlement(host, item, result.exiled_refs)
    return intent.player, result.exiled_refs


def schedule_spawn_settlement(host, item, refs):
    rows = [host._resolve_object(item.controller, ref, zones={"exile"}) for ref in refs]
    if any(card.owner != item.controller or not card.is_card_object for card in rows):
        raise ValueError("Spawn settlement lost a physical card from its owned library")
    receipt = thaw_value(validate_spawn_receipt({
        "schema_version": 1, "controller": item.controller, "semantic_key": item.semantic_key,
        "objects": [{"object_id": card.object_id, "logical_object_id": card.logical_object_id, "ref": card.ref}
                    for card in rows],
    }))
    label = "Settle the remaining exiled cards"
    instruction = {"op": "choose_objects", "player": item.controller, SETTLEMENT_FIELD: 2, RECEIPT_FIELD: receipt}
    host.schedule_delayed_trigger(
        controller=item.controller, label=label, event_kind="step.begin",
        condition={IF_ANY_FIELD: receipt},
        stack_template={"label": label, "context": {"dynamic_effects": [instruction]}},
        source_object_id=item.card_object_id, referred_object_ids=tuple(card.object_id for card in rows),
    )


def spawn_end_step_matches(host, trigger, context):
    receipt = validate_spawn_receipt(trigger.condition[IF_ANY_FIELD])
    if (set(trigger.condition) != {IF_ANY_FIELD} or trigger.controller != receipt["controller"] or not trigger.once
            or trigger.event_kind != "step.begin"):
        raise ValueError("Spawn settlement lost its original next end step")
    if (context.get("phase"), context.get("step")) != ("ending", "end_step"):
        return None
    _current_program(host, receipt["semantic_key"])
    return bool(tuple(live_exile_rows(host.state, receipt)))


def bind_spawn_settlement(host, item, effect):
    receipt = validate_spawn_receipt(effect[RECEIPT_FIELD])
    _current_program(host, receipt["semantic_key"])
    original = {"op": "choose_objects", "player": item.controller, SETTLEMENT_FIELD: 2, RECEIPT_FIELD: receipt}
    original_effects = item.context.get("dynamic_effects") or ()
    if (item.kind != "triggered_ability" or item.context.get("event") != "step.begin"
            or not item.context.get("delayed_trigger_ref") or item.controller != receipt["controller"]
            or len(original_effects) != 1 or FrozenMap(original_effects[0]) != FrozenMap(original)
            or type(effect.get(SETTLEMENT_FIELD)) is not int or effect[SETTLEMENT_FIELD] != 2):
        raise ValueError("Spawn settlement requires its original delayed trigger and exile receipt")
    refs = [card.ref for card in live_exile_rows(host.state, receipt)]
    if not refs:
        return None
    bound = {**original, "_linked_settlement_refs": refs}
    if FrozenMap(effect) == FrozenMap(original):
        return bound
    if "_linked_landfall_orders" in effect and FrozenMap({
        key: value for key, value in effect.items() if key not in {"player", "_linked_landfall_orders"}
    }) == FrozenMap({key: value for key, value in bound.items() if key != "player"}):
        return effect
    raise ValueError("Spawn settlement changed its sealed batch")


def commit_spawn_settlement(host, intent):
    from ..rules.committed_quantity_followups import CommittedQuantityFollowup
    from ..zone_transitions import ZoneTransitionOwner

    rows = tuple(live_exile_rows(host.state, intent.spawn_receipt))
    if tuple(card.ref for card in rows) != intent.object_refs:
        raise ValueError("The original Spawn exile incarnations changed")
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "graveyard") for card in rows), reason=intent.reason,
        replacement_selections=tuple(thaw_value(value) for value in intent.replacement_selections),
        require_graveyard_order=True, graveyard_orders=intent.orders,
    )
    count = sum(card.zone == "graveyard" and card.owner == intent.actor for card in moved)
    return CommittedQuantityFollowup((FrozenMap({
        "op": "create_token", "controller": intent.actor, "quantity": count, "name": "Eldrazi Spawn Token",
        "characteristics": {"type_line": "Token Creature — Eldrazi Spawn", "colors": [], "power": "0",
                            "toughness": "1", "literal_token_name_version": 1,
                            "activated_ability_profile": "sac_colorless_mana_v1",
                            "display_text": "Sacrifice this token: Add {C}."},
    }),) if count else ())
