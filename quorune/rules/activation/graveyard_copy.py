"""The payable X values of activations with exact mana-value targets."""

from __future__ import annotations

from ...target_source import ability_targeting_source
from ...targets import target_plan
from ..announced_mana_value_target import (
    bind_announced_mana_value_limit, has_activation_mana_value_equality,
)
from ..announced_artifact_copy import (
    announced_artifact_copy_target_schema, is_announced_artifact_copy_program,
)
from ..announced_graveyard_copy import (
    announced_graveyard_copy_target_schema, is_announced_graveyard_copy_program,
)
from .x_costs import activation_requirements, validate_activation_x_payment


def has_exact_announced_activation_target(program) -> bool:
    return (
        is_announced_graveyard_copy_program(program) or is_announced_artifact_copy_program(program)
        or has_activation_mana_value_equality(getattr(program, "target_schema", None))
    )


def bind_activation_targets(program, ability, schema, response, *, announced_x, target_limit):
    from ...activation_x import bind_activation_target_cardinality

    if is_announced_graveyard_copy_program(program):
        return announced_graveyard_copy_target_schema(announced_x)
    if is_announced_artifact_copy_program(program):
        return announced_artifact_copy_target_schema(announced_x)
    if has_activation_mana_value_equality(schema):
        return bind_announced_mana_value_limit(schema, announced_x, activation=True)
    return bind_activation_target_cardinality(ability, schema, response, target_limit=target_limit)


def activation_public_targets(host, seat, source, ability, program, schema):
    """Narrow public refs to choices the same paid activation can accept."""
    if has_activation_mana_value_equality(schema):
        schema = bind_announced_mana_value_limit(schema, 0, activation=True)
        del schema["mana_value"]
    acting = ability_targeting_source(host, source)
    public = host._public_target_schema(
        seat, schema, acting_source=acting, source_ref=source.ref, announcing_targets=True,
    )
    if public is None or not has_exact_announced_activation_target(program):
        return public

    group, = target_plan(schema).groups
    offered, = public["groups"]
    if offered["id"] != group.group_id:
        raise ValueError("Announced mana-value targeting requires its one target group")
    x_bounds = {}
    for ref in offered["legal_refs"]:
        mana_value = host._target_snapshot(ref)["mana_value"]
        if mana_value < 0 or not float(mana_value).is_integer():
            continue
        amount = int(mana_value)
        try:
            requirements, announced = activation_requirements(host, seat, source, ability, {"x": amount}, targets=(ref,))
            validate_activation_x_payment(host, seat, source, ability, requirements)
        except ValueError:
            continue
        if announced != amount:
            raise ValueError("The target's offered price lost its announced X")
        x_bounds[ref] = {"minimum": amount, "maximum": amount}
    if not x_bounds:
        return None
    refs = list(x_bounds)
    return {**public, "legal_refs": refs, "groups": [{**offered, "legal_refs": refs}],
            "x_bound_group": group.group_id, "x_bounds_by_ref": x_bounds}


def announced_activation_choice(public):
    choice = {"type": "integer", "label": "X", "minimum": 0, "default": 0}
    bounds = (public or {}).get("x_bounds_by_ref")
    if bounds:
        values = {bound["minimum"] for bound in bounds.values()}
        choice.update(minimum=min(values), default=min(values), maximum=max(values))
    return choice
