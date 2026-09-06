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
                )
            )
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
        if card.controller != seat or card.tapped or card.phased_out:
            continue
        data = host._effective_card_data(card)
        card_types, _, supertypes = host._type_parts(
            str(data.get("type_line") or "")
        )
        if (
            "creature" in card_types
            and summoning_sickness_prohibits_tap_or_untap_cost(
                host,
                card,
                as_though_haste=host._may_activate_creature_as_haste(
                    seat, card
                ),
            )
        ):
            continue
        record = host.card_record(card)
        if not record:
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
        mana_abilities = [
            ability
            for ability in host._activated_abilities(card)
            if ability.mana_ability
            and ability.tap_source
            and card.zone in ability.zones
        ]
        if any(
            ability.activation_limit is not None
            for ability in mana_abilities
        ):
            # Auto-payment plans currently identify a source and output, not
            # a specific ability. Usage-limited mana therefore remains an
            # explicit activation so the authoritative usage owner commits it.
            continue
        if mana_abilities and not any(
            activation_condition_status(host, seat, ability, card)[0]
            == "payable"
            for ability in mana_abilities
        ):
            continue
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
