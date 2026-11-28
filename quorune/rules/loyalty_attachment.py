"""Closed optional loyalty attachment and counter sequence."""

from ..replacement.immutable import FrozenMap


LOYALTY_ATTACHMENT_CAPABILITY = "activation.loyalty.optional_attach_creature_counter"
LOYALTY_ATTACHMENT_MECHANIC = "loyalty-optional-attach-creature-counter"
LOYALTY_ATTACHMENT_TEMPLATE = "loyalty-optional-attach-creature-counter-v1"
LOYALTY_ATTACHMENT_DEPENDENCIES = (
    LOYALTY_ATTACHMENT_CAPABILITY, "attachment.action.fixed_source",
    "counter.producer.fixed_permanent_target_set_effect", "target.revalidate_resolution",
)


def loyalty_attachment_target():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "controller_relation": "you", "up_to": 1,
    }


def loyalty_attachment_effects():
    return (
        {"op": "attach", "attachment_kind": "equipment", "source": "$source.zone_object", "target": "$target.0"},
        {"op": "place_counters_on_targets", "cards": "$targets", "maximum_targets": 1,
         "counter": "+1/+1", "amount": 1, "source": "$source"},
    )


def loyalty_attachment_dependencies(*, effects, target_schema, active_zone, cost_schema):
    if (active_zone != "battlefield" or cost_schema is None or type(cost_schema.get("loyalty_delta")) is not int
            or cost_schema["loyalty_delta"] != 1
            or FrozenMap(target_schema or {}) != FrozenMap(loyalty_attachment_target())
            or tuple(FrozenMap(effect) for effect in effects)
            != tuple(FrozenMap(effect) for effect in loyalty_attachment_effects())):
        return ()
    return LOYALTY_ATTACHMENT_DEPENDENCIES


def loyalty_attachment_runtime_dependencies(program):
    marked = LOYALTY_ATTACHMENT_MECHANIC in program.coverage
    typed_pair = (program.event == "activate"
                  and (program.cost_schema or {}).get("loyalty_delta") == 1
                  and tuple(effect.get("op") for effect in program.effects) == ("attach", "place_counters_on_targets"))
    if not marked and not typed_pair:
        return ()
    required = loyalty_attachment_dependencies(
        effects=program.effects, target_schema=program.target_schema,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    catalog = tuple(handler.get("handler_id") for handler in program.handlers)
    if (not marked or not required or program.event != "activate" or catalog != ("activation.catalog.pinned.v1",)
            or program.provenance.get("template_id") != LOYALTY_ATTACHMENT_TEMPLATE
            or program.event_condition is not None):
        raise ValueError("Loyalty attachment requires one optional controlled creature and its exact counter")
    return required


def is_closed_loyalty_attachment_program(program):
    """Promote only the independently validated loyalty pair and dependencies."""
    try:
        required = loyalty_attachment_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
