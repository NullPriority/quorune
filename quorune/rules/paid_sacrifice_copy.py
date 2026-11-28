"""A positive committed creature-sacrifice price yields one cast-copy trigger."""

from collections.abc import Mapping
from copy import deepcopy

from ..errors import StateInvariantError
from ..model import StackItem
from ..replacement.immutable import thaw_value
from ..zone_trigger_events import ZoneTransitionKind
from .casting.optional_creature_payments import controlled_creature_payment, expand_counted_creature_payment_options


CAPABILITY = "stack.copy.cast_paid_creature_count"
MECHANIC = "cast-paid-creature-count-copy"
TEMPLATE = "spell-additional-cost-creature-count-copy-v1"
COST_FIELD = "creature_sacrifice_copy"
PAYMENT_FIELD = "creature_sacrifice_copy_payment"
SEMANTIC_KEY = "builtin:paid-creature-sacrifice-copy:v1"
_OPTION_MARKER = "+creature-sacrifice-copy:"
_BODY_TEMPLATE = "fixed-controller-draw-effect-sequence-v1"
_BODY = (
    {"op": "draw", "player": "$controller", "count": 1, "private": True},
    {"op": "lose_life", "player": "$controller", "amount": 1},
)


def cast_copy_cost_schema():
    return {COST_FIELD: 1}


def validate_cast_copy_cost_schema(schema):
    if not isinstance(schema, Mapping) or COST_FIELD not in schema:
        return False
    if type(schema[COST_FIELD]) is not int or thaw_value(schema) != cast_copy_cost_schema():
        raise ValueError("Cast-copy cost requires its exact versioned optional creature price")
    return True


def paid_sacrifice_copy_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    marked = isinstance(cost_schema, Mapping) and COST_FIELD in cost_schema
    if not marked and MECHANIC not in mechanics:
        return None
    try:
        if not validate_cast_copy_cost_schema(cost_schema) or target_schema is not None:
            return ()
    except ValueError:
        return ()
    if (MECHANIC not in mechanics or len(effects) != len(_BODY)
            or type(effects[0].get("count")) is not int or effects[0].get("private") is not True
            or type(effects[1].get("amount")) is not int or thaw_value(tuple(effects)) != list(_BODY)):
        return ()
    from .fixed_controller_effect_shapes import fixed_controller_effect_sequence_node_capabilities

    body = fixed_controller_effect_sequence_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
    )
    return (CAPABILITY, *body) if body else ()


def paid_sacrifice_copy_runtime_dependencies(program):
    required = paid_sacrifice_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if required is None:
        return ()
    if (not required or program.event != "resolve" or program.active_zone != "stack"
            or not program.ability_id.startswith("spell:") or program.destination != "graveyard"
            or program.handlers or program.event_condition is not None
            or program.provenance.get("template_id") != f"{TEMPLATE}+{_BODY_TEMPLATE}"):
        raise ValueError("A paid cast-copy result needs its whole ordinary spell and exact body")
    return required


def is_closed_paid_sacrifice_copy_program(program):
    try:
        required = paid_sacrifice_copy_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def fixed_creature_copy_price(count):
    return controlled_creature_payment(count)


def expand_paid_sacrifice_copy_options(host, seat, source, options, schema):
    if not validate_cast_copy_cost_schema(schema):
        return options
    return expand_counted_creature_payment_options(host, seat, source, options, marker=_OPTION_MARKER)


def _required_paid_count(option_id):
    marks = [part.removeprefix(_OPTION_MARKER[1:]) for part in option_id.split("+")
             if part.startswith(_OPTION_MARKER[1:])]
    if not marks:
        return 0
    if len(marks) != 1 or not marks[0].isascii() or not marks[0].isdecimal():
        raise StateInvariantError("Cast-copy paid option lost its exact declaration")
    count = int(marks[0])
    if count < 1 or marks[0] != str(count):
        raise StateInvariantError("Cast-copy paid option has an invalid sacrifice count")
    return count


def _paid_incarnations(value):
    if (not isinstance(value, (tuple, list)) or not value
            or any(type(identity) is not str or not identity for identity in value)
            or len(set(value)) != len(value)):
        raise StateInvariantError("Cast-copy trigger lost its distinct paid creatures")
    return tuple(value)


def paid_sacrifice_copy_trigger_items(host, *, spell, card, program, committed_costs):
    from ..storm import spell_copy_template

    if not program or not validate_cast_copy_cost_schema(program.cost_schema):
        return ()
    count = _required_paid_count(spell.context["cost_option"])
    if count == 0:
        return ()
    paid = _paid_incarnations([
        event[3] for event in committed_costs.deferred_events if event[-1] is ZoneTransitionKind.SACRIFICE
    ])
    if len(paid) != count:
        raise StateInvariantError("Cast-copy option differs from its committed sacrifice count")
    template = spell_copy_template(host, spell, program)
    if template.get("target_schema") is not None:
        raise StateInvariantError("The paid creature copy contract has no spell targets")
    ref = host._next_ref("S")
    return (StackItem(
        stack_id=host._stable_runtime_id("stack", ref), ref=ref, kind="triggered_ability",
        controller=spell.controller, label=f"{spell.label} — Sacrifice copy",
        source_object_id=card.object_id, semantic_key=SEMANTIC_KEY, visibility=list(host.seats),
        context={"copy_count": len(paid), "copy_template": template,
                 PAYMENT_FIELD: {"schema_version": 1, "paid_incarnations": list(paid)}},
    ),)


def validated_paid_sacrifice_copy_trigger(item):
    fact = item.context.get(PAYMENT_FIELD)
    if (item.semantic_key != SEMANTIC_KEY or not isinstance(fact, Mapping)
            or set(fact) != {"schema_version", "paid_incarnations"}
            or type(fact["schema_version"]) is not int or fact["schema_version"] != 1):
        raise StateInvariantError("Cast-copy trigger payment is malformed")
    paid = _paid_incarnations(fact["paid_incarnations"])
    count = item.context.get("copy_count")
    template = item.context.get("copy_template")
    if (type(count) is not int or count != len(paid) or not isinstance(template, Mapping)
            or template.get("target_schema") is not None):
        raise StateInvariantError("Cast-copy trigger lost its original copy specification")
    return count, deepcopy(dict(template))
