"""Resolve a public exile pool, scoped spell permission and one delayed return."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..impulse_access_model import ImpulseAccessDuration
from ..linked_exile_return_model import ExiledObjectIdentity
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effect_operations


EXILE_OPERATION = "exile_opponents_graveyards_for_turn_cast"
RETURN_OPERATION = "return_opponents_graveyard_exile_pool"
CAPABILITY = "casting.permission.opponents_graveyard_exile_pool"
MECHANIC = "opponents-graveyard-exile-pool-cast"
TEMPLATE = "opponents-graveyard-exile-pool-cast-v1"
INSTRUCTION = FrozenMap({"op": EXILE_OPERATION, "schema_version": 1, "player": "$controller"})
DELAYED_MARKER = "opponents_graveyard_exile_pool_due"
PERMISSION_FIELD = "opponents_graveyard_exile_cast"
REQUIREMENTS = (CAPABILITY, "zone.change.destination_replacement", "zone.linked_exile_return.fixed")


def validate_exile_pool_instruction(effect, *, actor="$controller"):
    if (not isinstance(effect, Mapping) or FrozenMap(effect) != FrozenMap({**INSTRUCTION, "player": actor})
            or type(effect.get("schema_version")) is not int):
        raise ValueError("Graveyard exile cast requires its complete controller-scoped instruction")


def graveyard_pool_cast_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    """Bind the graveyard-pool operation and its printed terminal self-exile."""
    if not any(effect.get("op") == EXILE_OPERATION for effect in effects):
        return None
    from .spell_self_exile import SELF_EXILE, terminal_spell_move_node_capabilities

    if SELF_EXILE.mechanic in mechanics:
        return terminal_spell_move_node_capabilities(
            effects=effects, target_schema=target_schema, mechanics=mechanics, cost_schema=cost_schema,
        )
    return exile_pool_node_capabilities(effects, target_schema, mechanics)


def exile_pool_node_capabilities(effects, target_schema, mechanics):
    if len(effects) != 1 or target_schema is not None or MECHANIC not in mechanics:
        return ()
    try:
        validate_exile_pool_instruction(effects[0])
    except (TypeError, ValueError):
        return ()
    return REQUIREMENTS


def exile_pool_runtime_dependencies(program):
    if EXILE_OPERATION not in nested_effect_operations(program.effects):
        return ()
    required = exile_pool_node_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.event_condition is not None or program.cost_schema is not None
            or not program.ability_id.startswith("spell:") or program.destination != "exile"):
        raise ValueError("Graveyard exile cast requires its whole self-exiling resolving spell")
    return required


def is_closed_exile_pool_program(program):
    from dataclasses import replace

    try:
        if (program.destination == "graveyard" and program.provenance.get("template_id") == TEMPLATE):
            # The terminal spell owner validates and projects the ordinary resolving body.
            program = replace(program, destination="exile")
        required = exile_pool_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def current_graveyard_exile_cast_permission(state, seat, card):
    player = state.players.get(seat)
    raw = card.annotations.get(PERMISSION_FIELD)
    if (player is None or not player.in_game or card.zone != "exile" or card.face_down
            or not card.is_card_object or card.owner == seat or not isinstance(raw, Mapping)):
        return None
    grant = raw.get(seat)
    if (not isinstance(grant, Mapping) or set(grant) != {"schema_version", "turn_sequence", "identity"}
            or type(grant["schema_version"]) is not int or grant["schema_version"] != 1
            or type(grant["turn_sequence"]) is not int or grant["turn_sequence"] != state.turn_sequence
            or grant["identity"] != card.logical_object_id):
        return None
    return {"player": seat, "zone": "exile", "without_mana_cost": False, "allow_land": False, "allow_spell": True,
            "duration": ImpulseAccessDuration.END_OF_TURN.value, "turn_sequence": grant["turn_sequence"],
            "allow_any_mana_type": True}


def _eligible_originals(state, objects):
    if not isinstance(objects, (list, tuple)):
        raise ValueError("The delayed return requires its original exile identities")
    identities = tuple(ExiledObjectIdentity.from_dict(identity) for identity in objects)
    if len({identity.object_id for identity in identities}) != len(identities):
        raise ValueError("The delayed return requires unique exile incarnations")
    return tuple(state.cards[item.object_id] for item in identities if item.matches_exiled_card(state.cards.get(item.object_id)))


def validate_graveyard_exile_pool_return(effect):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "objects"}
            or effect["op"] != RETURN_OPERATION or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or not isinstance(effect["objects"], (list, tuple)) or not effect["objects"]):
        raise ValueError("The graveyard exile return requires its exact delayed instruction")
    identities = tuple(ExiledObjectIdentity.from_dict(identity) for identity in effect["objects"])
    if len({identity.object_id for identity in identities}) != len(identities):
        raise ValueError("The graveyard exile return requires unique objects")
    return identities


def check_exile_pool_end_step(state, trigger, context):
    if trigger.condition != {DELAYED_MARKER: 1} or type(trigger.condition[DELAYED_MARKER]) is not int or not trigger.once:
        raise ValueError("The exile return requires an exact one-shot next end step")
    if (context.get("phase"), context.get("step")) != ("ending", "end_step"):
        return None
    rows = trigger.stack_template.get("context", {}).get("dynamic_effects")
    if not isinstance(rows, (list, tuple)) or len(rows) != 1:
        raise ValueError("The exile return lost its delayed linked effect")
    instruction, = rows
    identities = validate_graveyard_exile_pool_return(instruction)
    if tuple(trigger.referred_object_ids) != tuple(row.object_id for row in identities):
        raise ValueError("The exile return lost its original linked set")
    return bool(_eligible_originals(state, instruction["objects"]))


def _originals_in_exile(cards, actor):
    return tuple(card for card in cards if card.zone == "exile" and card.owner != actor and card.is_card_object)


def apply_exile_pool_cast(host, effect, *, actor, operation, reason):
    from ..trigger_processing import schedule_delayed_trigger
    from ..zone_transitions import ZoneTransitionOwner

    try:
        body = {key: value for key, value in thaw_value(effect).items()
                if key not in {"reason", "_runtime_source", "_replacement_selections"}}
        validate_exile_pool_instruction(body, actor=actor)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
        if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
                or item.context.get("currently_resolving") is not True or item.targets):
            raise ValueError("Graveyard exile cast requires its original resolving spell")
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not is_closed_exile_pool_program(program)):
            raise ValueError("Graveyard exile cast requires a trusted complete spell")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    changes = tuple((object_id, "exile")
                    for seat in host.active_seats if seat != actor
                    for object_id in host.state.players[seat].zones["graveyard"]
                    if host.state.cards[object_id].is_card_object)
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        changes, reason=reason, log=True, replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    ) if changes else ()
    exiled = _originals_in_exile(moved, actor)
    if not exiled:
        return ()
    for card in exiled:
        old = card.annotations.get(PERMISSION_FIELD)
        card.annotations[PERMISSION_FIELD] = {**(old if isinstance(old, Mapping) else {}), actor: {
            "schema_version": 1, "turn_sequence": host.state.turn_sequence, "identity": card.logical_object_id,
        }}
    identities = [ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id).to_dict() for card in exiled]
    schedule_delayed_trigger(
        host, controller=actor, label="Return the remaining exiled graveyard cards", event_kind="step.begin",
        condition={DELAYED_MARKER: 1}, source_object_id=source.object_id,
        referred_object_ids=tuple(card.object_id for card in exiled), once=True,
        stack_template={"context": {"dynamic_effects": [
            {"op": RETURN_OPERATION, "schema_version": 1, "objects": identities},
        ]}},
    )
    return tuple(card.ref for card in exiled)


def apply_return_exile_pool(host, effect, *, actor, operation, reason):
    from ..zone_transitions import ZoneTransitionOwner

    try:
        raw = {key: value for key, value in thaw_value(effect).items()
               if key not in {"reason", "_runtime_source", "_replacement_selections"}}
        validate_graveyard_exile_pool_return(raw)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
        if (item is None or item.kind != "triggered_ability" or item.controller != actor
                or item.context.get("currently_resolving") is not True
                or item.context.get("event") != "step.begin" or not item.context.get("delayed_trigger_ref")
                or thaw_value(item.context.get("dynamic_effects")) != [raw]):
            raise ValueError("Graveyard exile return requires its original delayed trigger")
        originals = _eligible_originals(host.state, raw["objects"])
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if not originals:
        return ()
    return ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "graveyard") for card in originals), reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    )
