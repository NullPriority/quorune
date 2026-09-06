from __future__ import annotations

from typing import Any, Protocol

from ...abilities import ActivatedAbility, reduced_requirements
from ...activation_mana_cost import payable_activation_mana_options
from ...crew import available_crew_power
from ...haste import summoning_sickness_prohibits_tap_or_untap_cost
from ...station import (
    StationAbilityError,
    station_candidates,
    station_cost_choice,
)
from ...semantic_runtime.activation_restrictions import (
    nonmana_activation_prohibited_by_chosen_name,
)
from .conditions import activation_condition_status


class ActivationAvailabilityHost(Protocol):
    state: Any

    def _loyalty_cost_modifier_present(self) -> bool: ...

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _may_activate_creature_as_haste(self, seat: str, card: Any) -> bool: ...

    def _ability_choice_payable(
        self, seat: str, source: Any, ability: ActivatedAbility
    ) -> bool: ...

    def _crew_threshold(self, ability: ActivatedAbility) -> int | None: ...

    def _crew_candidates(self, seat: str, source: Any) -> list[Any]: ...

    def _legendary_creatures_controlled(self, seat: str) -> int: ...

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: dict[str, int],
        *,
        exclude_sources: set[str] | None = None,
        spend_context: str | None = None,
        snow_required: int = 0,
    ) -> bool: ...


def activation_availability(
    host: ActivationAvailabilityHost,
    seat: str,
    card: Any,
    ability: ActivatedAbility,
) -> tuple[str, str | None]:
    """Return the read-only payable status for one typed ability."""

    player = host.state.players[seat]
    zone = card.zone
    if zone not in ability.zones:
        return "unavailable", "wrong_zone"
    if not ability.compiled_cost:
        return "unresolved", "unresolved_cost_semantics"
    if nonmana_activation_prohibited_by_chosen_name(host, card, ability):
        return "unavailable", "named_ability_prohibition"
    condition_status, condition_reason = activation_condition_status(
        host,
        seat, ability, card
    )
    if condition_status != "payable":
        return condition_status, condition_reason
    if ability.sorcery_speed and not _main_phase_timing(host, seat):
        return "unavailable", "sorcery_timing"
    loyalty_status = _loyalty_availability(host, seat, card, ability)
    if loyalty_status is not None:
        return loyalty_status
    source_status = _source_cost_availability(host, seat, card, ability)
    if source_status is not None:
        return source_status
    if ability.life_payment and player.life < ability.life_payment:
        return "unpayable", "insufficient_life"
    if ability.energy_payment and player.energy < ability.energy_payment:
        return "unpayable", "insufficient_energy"
    station_choice = station_cost_choice(ability)
    if station_choice is not None:
        try:
            candidates = station_candidates(host, seat, card)
        except StationAbilityError:
            return "unresolved", "unresolved_station_power"
        if not candidates:
            return "unpayable", "station_cost_object_unavailable"
    elif ability.choices and not host._ability_choice_payable(
        seat, card, ability
    ):
        return "unpayable", "mandatory_cost_object_unavailable"
    crew_threshold = host._crew_threshold(ability)
    if crew_threshold is not None and available_crew_power(
        host._crew_candidates(seat, card)
    ) < crew_threshold:
        return "unpayable", "insufficient_crew_power"
    if ability.mana_cost_options:
        if not payable_activation_mana_options(host, seat, card, ability):
            return "unpayable", "complex_mana_cost_unpayable"
    else:
        requirements = reduced_requirements(
            ability,
            legendary_creatures=host._legendary_creatures_controlled(seat),
        )
        excluded = {card.object_id} if ability.tap_source else set()
        if sum(requirements.values()) and not host._cost_is_affordable(
            seat, requirements, exclude_sources=excluded
        ):
            return "unpayable", "insufficient_mana"
    return "payable", None


def _main_phase_timing(host: ActivationAvailabilityHost, seat: str) -> bool:
    return bool(
        seat == host.state.active_player
        and not host.state.stack
        and (host.state.phase, host.state.step)
        in {
            ("precombat_main", "main"),
            ("postcombat_main", "main"),
        }
    )


def _loyalty_availability(
    host: ActivationAvailabilityHost,
    seat: str,
    card: Any,
    ability: ActivatedAbility,
) -> tuple[str, str] | None:
    if ability.loyalty_delta is None:
        return None
    if host._loyalty_cost_modifier_present():
        return "unresolved", "unresolved_loyalty_cost_modification"
    if not _main_phase_timing(host, seat):
        return "unavailable", "loyalty_timing"
    if card.annotations.get("loyalty_activated_turn_sequence") == host.state.turn_sequence:
        return "unavailable", "loyalty_already_activated"
    if (
        ability.loyalty_delta < 0
        and int(card.counters.get("loyalty", 0)) < -ability.loyalty_delta
    ):
        return "unpayable", "insufficient_loyalty"
    return None


def _source_cost_availability(
    host: ActivationAvailabilityHost,
    seat: str,
    card: Any,
    ability: ActivatedAbility,
) -> tuple[str, str] | None:
    zone = card.zone
    if ability.tap_source:
        if zone != "battlefield":
            return "unavailable", "tap_cost_wrong_zone"
        if card.tapped:
            return "unavailable", "source_tapped"
    if ability.untap_source and (
        zone != "battlefield"
        or not card.tapped
        or int(card.counters.get("stun", 0)) > 0
    ):
        return "unavailable", "untap_cost_unavailable"
    if (
        (ability.tap_source or ability.untap_source)
        and zone == "battlefield"
        and summoning_sickness_prohibits_tap_or_untap_cost(
            host,
            card,
            as_though_haste=host._may_activate_creature_as_haste(seat, card),
        )
    ):
        return "unavailable", "summoning_sickness"
    if ability.discard_source and zone != "hand":
        return "unavailable", "discard_source_wrong_zone"
    if ability.sacrifice_source and zone != "battlefield":
        return "unavailable", "sacrifice_source_wrong_zone"
    return None


__all__ = ["ActivationAvailabilityHost", "activation_availability"]
