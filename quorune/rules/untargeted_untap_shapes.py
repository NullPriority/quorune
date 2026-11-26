"""Executable shape and independent runtime minimum for land untap choices."""
from collections.abc import Mapping

from ..untargeted_untap_model import (
    FixedLandUntapChoiceSpec, UNTARGETED_UNTAP_OPERATION,
    UNTARGETED_UNTAP_CAPABILITY, UNTARGETED_UNTAP_MECHANIC,
)


def untargeted_untap_payload_dependencies(value):
    if not isinstance(value, Mapping) or value.get('op') != UNTARGETED_UNTAP_OPERATION:
        return ()
    spec = FixedLandUntapChoiceSpec.from_effect(value)
    if value["player"] != ("$context.player" if spec.scheduled else "$controller"):
        raise ValueError("Land untap requires its resolving controller or exact scheduled participant")
    return (UNTARGETED_UNTAP_CAPABILITY, 'permanent.untap.effect')


def scheduled_land_untap_candidates(host, actor):
    from .untap_permissions import untap_is_permitted

    return tuple(sorted(
        row.ref for row in host.affected_permanent_object_rows(actor)
        if row.zone == "battlefield" and row.controller == actor and row.tapped and not row.phased_out
        and "land" in row.types and untap_is_permitted(host, host.state.cards[row.object_id])
    ))


def fixed_land_untap_choice_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or UNTARGETED_UNTAP_MECHANIC not in mechanic_ids:
        return ()
    try:
        return untargeted_untap_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_fixed_land_untap_choice_program(program):
    required = fixed_land_untap_choice_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
