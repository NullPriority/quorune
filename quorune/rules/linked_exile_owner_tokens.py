"""Resolve a source-departure token result using surviving linked exiles."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..fixed_token_production import (
    FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, FIXED_DISTRIBUTED_TOKEN_OPERATION, TOKEN_LITERAL_NAME_VERSION_FIELD,
)
from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY
from ..linked_exile_return_model import validate_linked_ability_binding
from ..replacement.immutable import FrozenMap


CAPABILITY = "token.creation.linked_exile_owner_mana_value"
TEMPLATE = "linked-exile-owner-mana-value-tokens-v1"
FIELD = "linked_exile_owner_token_version"
OWNERS_FIELD = "linked_exile_token_owners"
REQUIREMENTS = (
    CAPABILITY, LINKED_ABILITY_EXILE_CAPABILITY, FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID,
    "token.creation.fixed_definition", "trigger.effect.fixed_event",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap",
)


def source_linked_token_effect(pair):
    validate_linked_ability_binding(pair)
    return {"op": FIXED_DISTRIBUTED_TOKEN_OPERATION, FIELD: 1, "binding_id": pair}


def _validate_effect(effect):
    if (not isinstance(effect, Mapping) or type(effect.get(FIELD)) is not int
            or FrozenMap(effect) != FrozenMap(source_linked_token_effect(effect.get("binding_id")))):
        raise ValueError("Linked owner tokens require their original exile ability pair")


def linked_exile_owner_token_payload_dependencies(effect):
    _validate_effect(effect)
    return REQUIREMENTS


def linked_exile_owner_token_dependencies(program):
    if (CAPABILITY not in program.capability_dependencies
            and not any(FIELD in effect for effect in program.effects)):
        return ()
    if ((program.event, program.active_zone) != ("permanent.leave.self", "battlefield")
            or program.provenance.get("template_id") != TEMPLATE or len(program.effects) != 1
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.event_condition is not None or program.trigger_limit is not None):
        raise ValueError("Linked owner tokens require their original complete departure trigger")
    return linked_exile_owner_token_payload_dependencies(program.effects[0])


def _token(power):
    return {"op": "create_token", "controller": "$controller", "name": "Illusion Token", "quantity": 1,
            "characteristics": {"type_line": "Token Creature — Illusion", TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
                                "colors": ["U"], "power": str(power), "toughness": str(power)}}


def _current_exiled_mana_value(host, card):
    if card.face_down:
        return 0
    # Current exile uses the printed card value, including the front of a double-faced card.
    record = host.card_record(card)
    if record is None:
        raise ValueError("A linked exile requires its pinned card characteristics")
    value = float(record.mana_value)
    if not value.is_integer() or value < 0:
        raise ValueError("Linked exile token sizes require ordinary integral mana values")
    return int(value)


def bind_linked_exile_owner_tokens(host, item, effect):
    from ..linked_ability_exile_state import current_linked_ability_exile

    instruction = {key: value for key, value in effect.items() if key != "_replacement_selections"}
    _validate_effect(instruction)
    program = host.semantics.get(item.semantic_key)
    if (item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not linked_exile_owner_token_dependencies(program)
            or FrozenMap(program.effects[0]) != FrozenMap(instruction)):
        raise ValueError("Linked owner tokens require their resolving source-departure ability")
    source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    links = current_linked_ability_exile(host, source=source, ability_pair=instruction["binding_id"])
    cards = tuple(host.state.cards[link.object_id] for link in links)
    owners = {card.owner for card in cards}
    creators = [seat for seat in host.apnap_order() if seat in owners]
    if not creators:
        return None
    total = sum(_current_exiled_mana_value(host, card) for card in cards)
    return {
        "op": FIXED_DISTRIBUTED_TOKEN_OPERATION, "controller": item.controller, "token": _token(total),
        OWNERS_FIELD: creators,
        **({"_replacement_selections": effect["_replacement_selections"]} if "_replacement_selections" in effect else {}),
    }


def resolved_linked_exile_token_owners(effect, apnap):
    """Validate the materialized creator list for the ordinary distributed intent."""
    owners = effect[OWNERS_FIELD]
    token = effect["token"]
    power = token["characteristics"]["power"]
    if (set(effect) != {"op", "controller", "token", OWNERS_FIELD}
            or effect["op"] != FIXED_DISTRIBUTED_TOKEN_OPERATION
            or type(power) is not str or not power.isascii() or not power.isdecimal()
            or FrozenMap(token) != FrozenMap(_token(int(power)))
            or not isinstance(owners, (list, tuple)) or not owners
            or tuple(owners) != tuple(seat for seat in apnap if seat in owners)):
        raise ValueError("A linked owner result requires its identical tokens and distinct current owners")
    return tuple(owners)
