from __future__ import annotations

"""Read-only typed mana-source discovery for authoritative auto-payment."""

from typing import Any, Mapping, Protocol, Sequence

from .abilities import ActivatedAbility
from .haste import summoning_sickness_prohibits_tap_or_untap_cost
from .mana import ManaMode, ManaSource
from .mana_ability_runtime import (
    payable_mana_modes,
    typed_mana_modes_for_abilities,
)
from .rules.activation.conditions import activation_condition_status


class ManaSourceDiscoveryHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _may_activate_creature_as_haste(
        self, seat: str, card: Any
    ) -> bool: ...

    def display_name(self, object_id: str) -> str: ...

    def card_record(self, card: Any) -> Any: ...

    def _recordless_mana_modes(
        self, seat: str, card: Any
    ) -> Sequence[ManaMode]: ...

    def _activated_abilities(
        self, card: Any
    ) -> tuple[ActivatedAbility, ...]: ...

    def _mana_restriction_allows(
        self, restriction: str, spend_context: str | None
    ) -> bool: ...


def _unrestricted_when_payable(
    host: ManaSourceDiscoveryHost,
    modes: tuple[ManaMode, ...],
    *,
    spend_context: str | None,
) -> tuple[ManaMode, ...]:
    result: list[ManaMode] = []
    for mode in modes:
        restriction = mode.restriction or None
        if restriction is None:
            result.append(mode)
            continue
        if host._mana_restriction_allows(restriction, spend_context):
            result.append(
                ManaMode(
                    mode.bundle,
                    conditional=False,
                    restriction=mode.restriction,
                    side_effects=mode.side_effects,
                    requires_choice=mode.requires_choice,
                    tap_source=mode.tap_source,
                )
            )
    return tuple(result)


def mana_plan_abilities(host: Any, seat: str, card: Any, *, card_types=None) -> tuple[ActivatedAbility, ...]:
    """Shared source eligibility for tap mana and one-use self-sacrifice mana."""
    if card.zone!='battlefield' or card.controller!=seat or card.phased_out:
        return ()
    abilities=tuple(ability for ability in host._activated_abilities(card)
        if ability.mana_ability and card.zone in ability.zones)
    blocked_tap=card.tapped
    if not blocked_tap and any(ability.tap_source for ability in abilities):
        if card_types is None:
            card_types=host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]
        if 'creature' in card_types:
            blocked_tap=summoning_sickness_prohibits_tap_or_untap_cost(host,card,
                as_though_haste=host._may_activate_creature_as_haste(seat,card))
    result=[]
    for ability in abilities:
        if ability.tap_source:
            if blocked_tap:
                continue
        elif not (ability.sacrifice_source and not any((sum(ability.mana.values()),ability.choices,
                ability.untap_source,ability.discard_source,ability.exile_source,ability.life_payment,
                ability.energy_payment,ability.random_discard_payment,ability.loyalty_delta is not None,
                ability.source_counter_removal_cost is not None,ability.fixed_mana_result is not None))):
            continue
        result.append(ability)
    return tuple(result)


def available_mana_sources(
    host: ManaSourceDiscoveryHost,
    seat: str,
    *,
    spend_context: str | None = None,
) -> list[ManaSource]:
    """Discover payable modes without mutating game state."""

    sources: list[ManaSource] = []
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != seat or card.phased_out:
            continue
        data = host._effective_card_data(card)
        card_types, _, supertypes = host._type_parts(
            str(data.get("type_line") or "")
        )
        record = host.card_record(card)
        if not record:
            if card.tapped or ('creature' in card_types and summoning_sickness_prohibits_tap_or_untap_cost(
                    host,card,as_though_haste=host._may_activate_creature_as_haste(seat,card))):
                continue
            compiled_modes = host._recordless_mana_modes(seat, card)
            if compiled_modes:
                sources.append(
                    ManaSource(
                        object_id,
                        card.ref,
                        host.display_name(object_id),
                        tuple(compiled_modes),
                        snow="snow" in supertypes,
                    )
                )
            continue
        mana_abilities = mana_plan_abilities(host,seat,card,card_types=card_types)
        if any(
            ability.activation_limit is not None
            for ability in mana_abilities
        ):
            # Auto-payment plans currently identify a source and output, not
            # a specific ability. Usage-limited mana therefore remains an
            # explicit activation so the authoritative usage owner commits it.
            continue
        mana_abilities=tuple(ability for ability in mana_abilities
            if activation_condition_status(host,seat,ability,card)[0]=='payable')
        modes = payable_mana_modes(
            typed_mana_modes_for_abilities(host, seat, card, mana_abilities)
        )
        modes = _unrestricted_when_payable(
            host,
            modes,
            spend_context=spend_context,
        )
        if modes:
            sources.append(
                ManaSource(
                    object_id,
                    card.ref,
                    host.display_name(object_id),
                    modes,
                    snow="snow" in supertypes,
                )
            )
    return sources


__all__ = ["ManaSourceDiscoveryHost", "available_mana_sources"]
