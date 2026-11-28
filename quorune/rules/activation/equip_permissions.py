from __future__ import annotations

"""Current Equip timing and the optional alternate cost of the first activation."""

from dataclasses import dataclass, fields

from ...abilities import ActivatedAbility

from ...semantic_runtime.action_permissions import ActionPermissionKind, controller_has_action_permission
from ...turn_history import equip_activated_this_turn, record_first_equip_activation
from .ability_kinds import activation_matches_cost_scope
from .model import ActivationProposalError

EQUIP_PAYMENT_FIELD = "equip_payment"
EQUIP_PAYMENT_OPTIONS = "equip_payment_options"
PRINTED_EQUIP_PAYMENT = "printed"
ZERO_EQUIP_PAYMENT = "zero"


@dataclass(frozen=True, slots=True, kw_only=True)
class ZeroEquipPrice(ActivatedAbility):
    printed_equip: ActivatedAbility


def _is_equip(host, source, ability):
    return not ability.reconfigure and activation_matches_cost_scope(host, source, ability, "equip")


def _own_turn_equip(host, seat, source, ability):
    return seat == host.state.active_player and _is_equip(host, source, ability)


def equip_instant_timing(host, seat, source, ability):
    return _own_turn_equip(host, seat, source, ability) and controller_has_action_permission(
        host, seat, ActionPermissionKind.ACTIVATE_EQUIP_AT_INSTANT,
    )


def zero_equip_price(host, seat, source, ability):
    """Use {0} as the base price while retaining target, speed and usage restrictions."""
    if (not _own_turn_equip(host, seat, source, ability)
            or equip_activated_this_turn(host.state, seat) is not False
            or not controller_has_action_permission(host, seat, ActionPermissionKind.FIRST_EQUIP_ZERO)):
        return None
    # The typed Equip catalogue owns which nonmana symbols are part of Equip.
    # Additional costs and cost modifiers still use the usual activation owners.
    printed_fields = {field.name: getattr(ability, field.name) for field in fields(ActivatedAbility)}
    printed_fields.update(mana={}, mana_cost_options=(), life_payment=0, energy_payment=0,
                          choices=(), source_counter_removal_cost=None)
    return ZeroEquipPrice(**printed_fields, printed_equip=ability)


def selected_equip_price(host, seat, source, ability, selection):
    if selection is None:
        return ability
    if type(selection) is not str or selection not in {PRINTED_EQUIP_PAYMENT, ZERO_EQUIP_PAYMENT}:
        raise ActivationProposalError("Select a legal Equip price", reason="invalid_equip_payment")
    alternate = zero_equip_price(host, seat, source, ability)
    if alternate is None:
        raise ActivationProposalError("The first Equip price is unavailable", reason="equip_payment_unavailable")
    return alternate if selection == ZERO_EQUIP_PAYMENT else ability


def record_committed_equip_activation(host, seat, source, ability, source_incarnation):
    history = getattr(host.state, "turn_history", None)
    if (getattr(history, "equip_activation_history_version", None) == 1
            and _own_turn_equip(host, seat, source, ability)):
        record_first_equip_activation(host, seat, source_incarnation)
