"""Closed authority for an Aura returning attached to the original entrant."""

from collections.abc import Mapping

from ..replacement.immutable import freeze_value, thaw_value


GRAVEYARD_AURA_ENTRY_CAPABILITY = "zone.return.triggered_graveyard_aura_entry"
GRAVEYARD_AURA_ENTRY_FIELD = "graveyard_aura_entry"
GRAVEYARD_AURA_ENTRY_TEMPLATE = "fixed-graveyard-aura-attached-entry-v1"
GRAVEYARD_AURA_ENTRY_CAPABILITIES = (
    GRAVEYARD_AURA_ENTRY_CAPABILITY, "zone.self_move.activated", "attachment.aura.simple_object",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap", "zone.change.destination_replacement",
    "effect.choice.optional_fixed",
)


def graveyard_aura_entry_condition(minimum: int) -> dict:
    if type(minimum) is not int or minimum < 1:
        raise ValueError("An Aura return needs a positive entering-creature mana threshold")
    return {"all": [
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"field": "mana_value", "op": "gte", "value": minimum},
    ]}


def graveyard_aura_entry_effect() -> dict:
    return {"op": "self_zone_move", "origin": "graveyard", "destination": "battlefield", "tapped": False,
            "source_form": "card", GRAVEYARD_AURA_ENTRY_FIELD: {
                "ref": "$context.card", "logical_object_id": "$context.card_object_identity",
            }}


def optional_graveyard_aura_entry() -> dict:
    return {"op": "offer_optional_effect", "player": "$controller", "effects": [graveyard_aura_entry_effect()]}


def graveyard_aura_entry_runtime_dependencies(program) -> tuple[str, ...]:
    from .effect_shape_walkers import nested_effects

    marked = program.provenance.get("template_id") == GRAVEYARD_AURA_ENTRY_TEMPLATE
    if not marked and not any(GRAVEYARD_AURA_ENTRY_FIELD in effect for effect in nested_effects((*program.effects, *program.handlers))):
        return ()
    condition = thaw_value(freeze_value(program.event_condition))
    try:
        minimum = condition["all"][1]["value"]
        expected = graveyard_aura_entry_condition(minimum)
    except (TypeError, KeyError, IndexError, ValueError) as exc:
        raise ValueError("Graveyard Aura entry requires its complete creature-entry condition") from exc
    if (
        not marked or (program.event, program.active_zone) != ("permanent.enter", "graveyard")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or condition != expected or thaw_value(freeze_value(program.effects)) != [optional_graveyard_aura_entry()]
    ):
        raise ValueError("Graveyard Aura entry requires the original entrant and one optional source move")
    return GRAVEYARD_AURA_ENTRY_CAPABILITIES


def is_closed_graveyard_aura_entry_program(program) -> bool:
    if program.provenance.get("template_id") != GRAVEYARD_AURA_ENTRY_TEMPLATE:
        return False
    try:
        required = graveyard_aura_entry_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def validate_bound_graveyard_aura_entry(effect: Mapping) -> tuple[str, str]:
    anchor = effect.get(GRAVEYARD_AURA_ENTRY_FIELD)
    raw = graveyard_aura_entry_effect()
    if (any(effect.get(key) != value or type(effect.get(key)) is not type(value)
            for key, value in raw.items() if key != GRAVEYARD_AURA_ENTRY_FIELD)
            or set(effect) - set(raw) - {"_replacement_selections", "_aura_target_ref"}
            or not isinstance(anchor, Mapping) or set(anchor) != {"ref", "logical_object_id"}
            or any(type(value) is not str or not value or value.startswith("$") for value in anchor.values())
            or effect.get("_aura_target_ref", anchor["ref"]) != anchor["ref"]):
        raise ValueError("Graveyard Aura entry lost its exact attachment occurrence")
    return anchor["ref"], anchor["logical_object_id"]


def prepare_optional_graveyard_aura_entry(host, item, raw_effect, effect):
    """Carry the one legal untargeted attachment into the existing optional choice."""
    children = raw_effect.get("effects", ())
    if len(children) != 1 or GRAVEYARD_AURA_ENTRY_FIELD not in children[0]:
        return effect
    if (thaw_value(freeze_value(raw_effect)) != optional_graveyard_aura_entry()
            or effect.get("player") != item.controller or item.context.get("event") != "permanent.enter"):
        raise ValueError("Graveyard Aura return changed its source or original entry event")
    child, = effect["effects"]
    reference, logical = validate_bound_graveyard_aura_entry(child)
    if (reference, logical) != (item.context.get("card"), item.context.get("card_object_identity")):
        raise ValueError("Graveyard Aura return changed its original entering object")
    source = host.state.cards.get(item.source_object_id)
    if (source is None or source.owner != item.controller or source.zone != "graveyard"
            or source.logical_object_id != item.context.get("source_logical_object_id")):
        return None
    if not any(card.ref == reference and card.logical_object_id == logical and card.zone == "battlefield"
               and not card.phased_out for card in host.state.cards.values()):
        return None
    from ..aura import legal_aura_target_refs

    enchant = host._compiled_enchant_spec(source)
    if enchant is None or reference not in legal_aura_target_refs(host, source, enchant,
                                                                 controller=item.controller, as_target=False):
        return None
    return {**effect, "effects": [{**child, "_aura_target_ref": reference}]}
