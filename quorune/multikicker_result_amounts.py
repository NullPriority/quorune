from __future__ import annotations

"""Linked immutable Multikicker payments consumed by existing scalar results."""

from collections.abc import Mapping

from .multikicker_model import MULTIKICKER_CAPABILITY, MULTIKICKER_FIELD, validate_multikicker_fact
from .query_effect_amount_model import PublicQueryAmountError
from .scalar_effect_amount_model import (
    ScalarAmountOrigin, scalar_amount_specs, SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_MECHANIC,
)

MULTIKICKER_AMOUNT_CAPABILITY = 'quantity_expression.multikicker_count'
MULTIKICKER_AMOUNT_MECHANIC = 'multikicker-count-amount'


def needs_multikicker_entry_fact(host, card):
    """Keep historical entry payloads unchanged for programs without this reader."""
    return card.zone == 'stack' and any(
        program.event == 'permanent.enter.self'
        and MULTIKICKER_AMOUNT_CAPABILITY in program.capability_dependencies
        and host.semantic_program_is_current_trusted(program)
        for program in host.semantics.programs_for_oracle(card.oracle_id))


def multikicker_amount_dependencies(effects, mechanics):
    amounts = scalar_amount_specs(effects)
    linked = tuple(spec for spec in amounts if spec.origin is ScalarAmountOrigin.MULTIKICKER_COUNT)
    if not linked:
        if MULTIKICKER_AMOUNT_MECHANIC in mechanics:
            raise ValueError('Multikicker result marker has no linked amount')
        return ()
    if (not {SCALAR_AMOUNT_MECHANIC, MULTIKICKER_AMOUNT_MECHANIC} <= set(mechanics)
            or len(linked) != len(amounts)
            or len({spec.multikicker.fingerprint for spec in linked}) != 1
            or len({spec.multikicker.instance_id for spec in linked}) != 1):
        raise ValueError('Multikicker results require one complete linked scalar instance')
    return (MULTIKICKER_AMOUNT_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, MULTIKICKER_CAPABILITY)


def multikicker_amount_runtime_dependencies(program):
    return multikicker_amount_dependencies(program.effects, program.coverage)


def resolve_multikicker_amount(spec, item):
    context = item.context
    if not isinstance(context, Mapping):
        raise PublicQueryAmountError('Multikicker result context is malformed')
    fact = context.get(MULTIKICKER_FIELD)
    if fact is None:
        return 0
    try:
        paid = validate_multikicker_fact(fact)
    except (TypeError, ValueError) as exc:
        raise PublicQueryAmountError(str(exc)) from exc
    if paid != spec.multikicker.payment_fact(paid['count']):
        raise PublicQueryAmountError('Multikicker result refers to a different paid instance')
    return paid['count']
