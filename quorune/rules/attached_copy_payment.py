"""Closed payment and fallback for a controlled source attachment copy."""

from collections.abc import Mapping

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..token_copy_recipes import TokenCopyRecipeSpec
from .token_creation_capability_shapes import (
    fixed_token_creation_node_capabilities, token_definition_payload_dependencies,
)

VERSION = 18
TEMPLATE = "fixed-attached-copy-payment-v1"
OPERATION = "offer_optional_mana_payment"
FALLBACK_FIELD = "fallback_if_no_copy_created"
ATTACHMENT = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2)
COPY_RECIPE = TokenCopyRecipeSpec("source_attachment", schema_version=3, attachment_kind="enchanted")
_PAYMENT_FIELDS = {"op", "schema_version", "player", "source", "payment", "cost", "effects", "declined_effects"}
_COPY_FIELDS = {"op", "controller", "quantity", "copy_spec", FALLBACK_FIELD}


def uses_attached_copy_payment(effect):
    return effect.get("op") == OPERATION and effect.get("schema_version") == VERSION


def attached_copy_payment(requirements, fallback):
    payment = FixedEffectPaymentSpec("mana", requirements=requirements)
    copy = {"op": "create_token", "controller": "$controller", "quantity": 1,
            "copy_spec": COPY_RECIPE.to_dict(), FALLBACK_FIELD: fallback}
    return {"op": OPERATION, "schema_version": VERSION, "player": "$controller",
            "source": ATTACHMENT.to_dict(), "payment": payment.to_dict(), "cost": dict(payment.requirements),
            "effects": [copy], "declined_effects": [fallback]}


def _fallback_capabilities(fallback, player):
    if not isinstance(fallback, Mapping) or fallback.get("controller") != player:
        raise ValueError("Copy fallback must create a token for the resolving controller")
    fields = fallback.get("characteristics")
    if (not isinstance(fields, Mapping)
            or set(fields) != {"type_line", "literal_token_name_version", "colors", "power", "toughness"}
            or not str(fields.get("type_line", "")).startswith("Token Creature — ")
            or type(fallback.get("quantity")) is not int or fallback["quantity"] != 1):
        raise ValueError("Copy fallback requires one fixed ordinary creature token")
    token = {**thaw_value(FrozenMap(fallback)), "controller": "$controller"}
    caps = fixed_token_creation_node_capabilities(effects=(token,), target_schema=None, mechanic_ids={"cr-111-tokens"})
    if not caps:
        raise ValueError("Copy fallback token definition is unsupported")
    return caps


def committed_copy_fallback(effect, *, player):
    """Validate the closed ordinary copy instruction before it commits."""
    clean = {key: value for key, value in effect.items() if key not in {"_runtime_source", "_replacement_selections"}}
    if (
        set(clean) != _COPY_FIELDS or clean.get("op") != "create_token" or clean.get("controller") != player
        or type(clean.get("quantity")) is not int or clean["quantity"] != 1
        or FrozenMap(clean.get("copy_spec", {})) != FrozenMap(COPY_RECIPE.to_dict())
    ):
        raise ValueError("Zero-copy fallback requires its original attached-object recipe")
    fallback = clean[FALLBACK_FIELD]
    _fallback_capabilities(fallback, player)
    return thaw_value(FrozenMap(fallback))


def attached_copy_payment_parts(effect):
    if (set(effect) != _PAYMENT_FIELDS or not uses_attached_copy_payment(effect)
            or type(effect["schema_version"]) is not int or effect["player"] != "$controller"
            or FrozenMap(effect["source"]) != FrozenMap(ATTACHMENT.to_dict())):
        raise ValueError("Attached copy payment requires the closed source and controller")
    payment = FixedEffectPaymentSpec.from_dict(effect["payment"])
    if payment.kind != "mana" or FrozenMap(effect["cost"]) != FrozenMap(payment.requirements):
        raise ValueError("Attached copy payment requires one fixed mana cost")
    effects, declined = effect["effects"], effect["declined_effects"]
    if not isinstance(effects, (tuple, list)) or len(effects) != 1 or not isinstance(declined, (tuple, list)) or len(declined) != 1:
        raise ValueError("Attached copy payment requires one copy and one fallback")
    copy, = effects
    fallback = committed_copy_fallback(copy, player="$controller")
    if FrozenMap(fallback) != FrozenMap(declined[0]):
        raise ValueError("Declined and unsuccessful copies must have the same fallback")
    return payment, copy, fallback


def attached_copy_payment_capabilities(effect):
    payment, copy, fallback = attached_copy_payment_parts(effect)
    return (*payment.capabilities, *token_definition_payload_dependencies(copy),
            *_fallback_capabilities(fallback, "$controller"))


def attached_copy_payment_program_dependencies(program, effect):
    controller_condition = {"field": "controller", "op": "eq", "value": "$source.controller"}
    if (len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(effect) or program.event != "land.enter"
            or FrozenMap(program.event_condition or {}) != FrozenMap(controller_condition) or program.active_zone != "battlefield"
            or program.handlers or program.cost_schema is not None or program.target_schema is not None
            or program.trigger_limit is not None):
        raise ValueError("Attached copy payment requires the original controlled land entry")
    return (*attached_copy_payment_capabilities(effect), "trigger.event.normalized_zone_change",
            "trigger.effect.fixed_event", "trigger.placement.apnap")
