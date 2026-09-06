from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .abilities import ActivatedAbility
from .errors import GameRuleError
from .haste import summoning_sickness_prohibits_tap_or_untap_cost
from .mana import ManaMode
from .mana_ability_runtime import (
    payable_mana_modes,
    typed_mana_modes_for_abilities,
)
from .mana_undo import (
    clear_mana_undo_stack,
    push_mana_undo,
    ReversibleManaActivation,
)
from .mana_mode_effects import apply_mana_mode_effects
from .util import normalize_mana_bundle
from .tap_state import set_permanent_tapped


class ManaActivationHost(Protocol):
    state: Any

    def _mana_output_for_ability(
        self,
        seat: str,
        source: Any,
        ability: ActivatedAbility,
        response: Mapping[str, Any],
    ) -> dict[str, int]: ...

    def _mana_modes_for_ability(
        self, seat: str, source: Any, ability: ActivatedAbility
    ) -> tuple[ManaMode, ...]: ...

    def _add_mana_to_pool(
        self,
        seat: str,
        bundle: Mapping[str, int],
        *,
        restriction: str | None = None,
        snow_source: bool = False,
    ) -> None: ...

    def _log(self, *args: Any, **kwargs: Any) -> Any: ...

    def _stabilize(self) -> bool: ...

    def _resolve_object(self, actor: str, ref: str, **kwargs: Any) -> Any: ...

    def card_record(self, card: Any) -> Any: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...

    def _may_activate_creature_as_haste(
        self, seat: str, card: Any
    ) -> bool: ...

    def _recordless_mana_modes(self, seat: str, card: Any) -> Sequence[ManaMode]: ...

    def _activated_abilities(self, card: Any) -> tuple[ActivatedAbility, ...]: ...

    def _activation_condition_status(
        self, seat: str, ability: ActivatedAbility, card: Any
    ) -> tuple[str, str | None]: ...

    def _mana_restriction_allows(
        self, restriction: str, spend_context: str | None
    ) -> bool: ...


def complete_mana_activation(
    host: ManaActivationHost,
    *,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
    response: Mapping[str, Any],
    origin: str,
    paid_objects: Sequence[str],
    payment_activations: Sequence[Mapping[str, Any]],
    source_was_snow: bool,
) -> None:
    """Commit one activated mana ability and its reversible UI boundary."""

    bundle = host._mana_output_for_ability(seat, source, ability, response)
    restriction = ability.mana_spend_restriction
    host._add_mana_to_pool(
        seat,
        bundle,
        restriction=restriction,
        snow_source=source_was_snow,
    )
    selected_mode = next(
        (
            mode
            for mode in host._mana_modes_for_ability(seat, source, ability)
            if normalize_mana_bundle(mode.bundle) == bundle
        ),
        None,
    )
    # Explicit activation costs were already committed transactionally by the
    # activation owner. ManaMode side effects are the equivalent cost plan used
    # only by automatic payment and must not be applied twice here.
    result_effects = tuple(
        effect
        for effect in (
            selected_mode.side_effects if selected_mode is not None else ()
        )
        if effect.get("op") == "damage_self"
    )
    if result_effects:
        apply_mana_mode_effects(
            host,
            seat,
            result_effects,
            source=source,
            payment_id=str(response.get("_mana_payment_id") or "") or None,
            replacement_selections_by_event=(
                response.get("_mana_replacement_selections")
                if isinstance(
                    response.get("_mana_replacement_selections"), Mapping
                )
                else None
            ),
        )
    host._log(
        seat,
        "mana.ability",
        f"{seat} activated {source.ref} {ability.ability_id} for mana.",
        {
            "source": source.ref,
            "ability": ability.ability_id,
            "from": origin,
            "bundle": {key: value for key, value in bundle.items() if value},
            "cost_objects": [
                host.state.cards[object_id].ref
                for object_id in paid_objects
            ],
        },
        importance=0,
        changed_objects=[source.object_id, *paid_objects],
        changed_players=[seat],
    )
    reversible = bool(
        ability.tap_source
        and ability.activation_limit is None
        and not sum(ability.mana.values())
        and not ability.choices
        and not any(
            (
                ability.untap_source,
                ability.discard_source,
                ability.sacrifice_source,
                ability.exile_source,
                ability.life_payment,
                ability.energy_payment,
                ability.loyalty_delta is not None,
                paid_objects,
                payment_activations,
                restriction,
                source_was_snow,
                selected_mode is not None and bool(selected_mode.side_effects),
            )
        )
        and source.zone == "battlefield"
    )
    if reversible:
        push_mana_undo(
            host.state.players[seat].stats,
            ReversibleManaActivation.create(
                source_object_id=source.object_id,
                source_logical_object_id=source.logical_object_id,
                source_ref=source.ref,
                ability_id=ability.ability_id,
                bundle=bundle,
                turn_sequence=host.state.turn_sequence,
                phase=host.state.phase,
                step=host.state.step,
                priority_epoch=host.state.priority_epoch,
            ),
        )
    else:
        clear_mana_undo_stack(host.state.players[seat].stats)
    if host._stabilize():
        clear_mana_undo_stack(host.state.players[seat].stats)
        return
    host.state.priority_player = seat
    host.state.priority_passes = []


def _mana_plan_modes(
    host: ManaActivationHost,
    seat: str,
    card: Any,
) -> tuple[ManaMode, ...]:
    record = host.card_record(card)
    if not record:
        modes = tuple(host._recordless_mana_modes(seat, card))
        if not modes:
            raise GameRuleError(f"{card.ref} has no compiled mana mode")
        return modes
    mana_abilities = [
        ability
        for ability in host._activated_abilities(card)
        if ability.mana_ability
        and ability.tap_source
        and card.zone in ability.zones
    ]
    if any(ability.activation_limit is not None for ability in mana_abilities):
        raise GameRuleError(
            f"{card.ref} has a usage-limited mana ability; activate it explicitly"
        )
    modes = payable_mana_modes(
        typed_mana_modes_for_abilities(host, seat, card, mana_abilities)
    )
    if not modes:
        raise GameRuleError(f"{card.ref} has no compiler-pinned mana mode")
    return modes


def _selected_plan_mode(
    host: ManaActivationHost,
    seat: str,
    card: Any,
    modes: Sequence[ManaMode],
    bundle: Mapping[str, int],
    *,
    allow_conditional: bool,
    spend_context: str | None,
) -> tuple[ManaMode, str | None]:
    mode = next(
        (
            value
            for value in modes
            if normalize_mana_bundle(value.bundle) == bundle
        ),
        None,
    )
    if mode is None:
        raise GameRuleError(
            f"Declared output is not a recognized mana mode of {card.printed_name}"
        )
    mana_abilities = [
        ability
        for ability in host._activated_abilities(card)
        if ability.mana_ability
        and ability.tap_source
        and card.zone in ability.zones
    ]
    if mana_abilities and not any(
        host._activation_condition_status(seat, ability, card)[0] == "payable"
        for ability in mana_abilities
    ):
        raise GameRuleError(
            f"{card.printed_name}'s mana ability has an unmet or unresolved activation condition"
        )
    compiled = mode.restriction or None
    restriction_allows = bool(
        compiled and host._mana_restriction_allows(compiled, spend_context)
    )
    if mode.requires_choice:
        raise GameRuleError(
            f"{card.printed_name}'s selected mana mode has a nonmana choice/cost; "
            "activate that Oracle ability explicitly."
        )
    if (
        mode.conditional
        and host.state.config.strict_mana
        and not restriction_allows
    ):
        raise GameRuleError(
            f"{card.printed_name}'s selected mana mode is conditional/restricted "
            "and has no compiled validator."
        )
    if (
        mode.conditional
        and not allow_conditional
        and not restriction_allows
    ):
        raise GameRuleError(
            f"{card.printed_name}'s selected mana mode requires an explicit condition"
        )
    return mode, compiled


def _commit_plan_mode(
    host: ManaActivationHost,
    seat: str,
    card: Any,
    mode: ManaMode,
    bundle: Mapping[str, int],
    restriction: str | None,
    snow_source: bool,
    *,
    payment_id: str | None,
    replacement_selections_by_event: Mapping[str, Any] | None,
) -> None:
    cost_effects = tuple(
        effect for effect in mode.side_effects
        if effect.get("op") == "sacrifice_source"
    )
    result_effects = tuple(
        effect for effect in mode.side_effects
        if effect.get("op") != "sacrifice_source"
    )
    set_permanent_tapped(
        host,
        card.ref,
        actor=seat,
        tapped=True,
        reason="mana ability cost",
        log=False,
    )
    apply_mana_mode_effects(
        host,
        seat,
        cost_effects,
        source=card,
        payment_id=payment_id,
        replacement_selections_by_event=replacement_selections_by_event,
    )
    host._add_mana_to_pool(
        seat,
        bundle,
        restriction=restriction,
        snow_source=snow_source,
    )
    apply_mana_mode_effects(
        host,
        seat,
        result_effects,
        source=card,
        payment_id=payment_id,
        replacement_selections_by_event=replacement_selections_by_event,
    )
    public_bundle = {key: value for key, value in bundle.items() if value}
    host._log(
        seat,
        "mana.produce",
        f"{seat} tapped {card.ref} for {public_bundle}.",
        {"source": card.ref, "bundle": public_bundle},
        importance=0,
        changed_objects=[card.object_id],
        changed_players=[seat],
    )


def complete_mana_plan_activations(
    host: ManaActivationHost,
    seat: str,
    activations: Sequence[Mapping[str, Any]],
    *,
    spend_context: str | None = None,
    payment_id: str | None = None,
    replacement_selections_by_event: Mapping[str, Any] | None = None,
) -> None:
    """Validate and commit an authoritative derived mana-payment plan."""

    for activation in activations:
        card = host._resolve_object(
            seat,
            str(activation["source"]),
            zones={"battlefield"},
            controlled_only=True,
        )
        if card.tapped:
            raise GameRuleError(f"{card.ref} is already tapped")
        data = host._effective_card_data(card)
        card_types, _subtypes, supertypes = host._type_parts(
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
            raise GameRuleError(f"{card.ref} is summoning sick")
        bundle = normalize_mana_bundle(activation.get("bundle"))
        modes = _mana_plan_modes(host, seat, card)
        mode, restriction = _selected_plan_mode(
            host,
            seat,
            card,
            modes,
            bundle,
            allow_conditional=bool(activation.get("allow_conditional")),
            spend_context=spend_context,
        )
        _commit_plan_mode(
            host,
            seat,
            card,
            mode,
            bundle,
            restriction,
            "snow" in supertypes,
            payment_id=payment_id,
            replacement_selections_by_event=replacement_selections_by_event,
        )


__all__ = [
    "ManaActivationHost",
    "complete_mana_activation",
    "complete_mana_plan_activations",
]
