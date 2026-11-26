from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .abilities import ActivatedAbility
from .errors import GameRuleError
from .fixed_mana_results import EXERT_MANA_SOURCE, GAIN_CONTROLLER_LIFE, PLACE_MANA_POINT_COUNTER
from .haste import summoning_sickness_prohibits_tap_or_untap_cost
from .mana import ManaMode, ManaStepInput
from .mana_ability_runtime import (
    activated_mana_spending_rule,
    mana_output_for_ability,
    payable_mana_modes,
    typed_mana_modes_for_abilities,
)
from .mana_undo import (
    clear_mana_undo_stack,
    push_mana_undo,
    ReversibleManaActivation,
)
from .mana_mode_effects import apply_mana_mode_effects
from .mana_spend_rules import is_source_bound_cast_mana
from .util import normalize_mana_bundle, pay_mana_from_pool
from .tap_state import set_permanent_tapped
from .mana_source_discovery import mana_plan_abilities
from .triggered_mana import (
    TapManaSource, commit_triggered_mana, queue_mana_tap_triggers, tap_mana_source, triggered_mana_base_response,
)
from .triggered_mana_model import TRIGGERED_MANA_OPTION_FIELD
from .rules.creature_ability_activation import (
    CreatureAbilityActivation, capture_creature_ability_activation, publish_creature_ability_activation,
)


class ManaActivationHost(Protocol):
    state: Any
    turn_priority: Any

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

    def _spendable_mana_pool(self, seat: str, spend_context: str | None) -> dict[str, int]: ...

    def _apply_mana_spend(self, seat: str, spent: Mapping[str, int], spend_context: str | None) -> None: ...


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
    source_information=None,
    mana_payment=None,
    trigger_source: TapManaSource | None = None,
    creature_activation: CreatureAbilityActivation | None = None,
) -> None:
    """Commit one activated mana ability and its reversible UI boundary."""

    from .mana_ability_runtime import tap_mana_production_factor
    from .rules.exert_mana import apply_mana_exert, is_exert_mana

    observed = publish_creature_ability_activation(host, creature_activation)
    if is_exert_mana(ability):
        apply_mana_exert(host, source, actor=seat)
    from .artifact_hand_mana_model import is_artifact_hand_mana
    artifact_selection = None
    if is_artifact_hand_mana(ability):
        from .rules.hand_artifact_mana import ArtifactHandManaSelection, commit_artifact_hand_mana_reveal

        artifact_selection = source_information
        if (not isinstance(artifact_selection, ArtifactHandManaSelection) or artifact_selection.actor != seat
                or artifact_selection.source.object_id != source.object_id):
            raise GameRuleError("Artifact hand mana lost its sealed activation selection")
        base_bundle = commit_artifact_hand_mana_reveal(host, artifact_selection, source.ref)
    else:
        base_bundle = mana_output_for_ability(host, seat, source, ability, triggered_mana_base_response(response),
            source_information=source_information)
    factor = tap_mana_production_factor(host, seat, trigger_source)
    bundle = {color: amount * factor for color, amount in base_bundle.items()}
    restriction = activated_mana_spending_rule(ability, source, seat)
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
            if normalize_mana_bundle(mode.bundle) == base_bundle
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
        if effect.get("op") in {"damage_self", "skip_actor_untap", "sacrifice_if_no_counters",
                                    GAIN_CONTROLLER_LIFE, PLACE_MANA_POINT_COUNTER}
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
    selection = response.get(TRIGGERED_MANA_OPTION_FIELD)
    triggered = commit_triggered_mana(host, seat, trigger_source, bundle,
        **({"selection": selection} if selection is not None else {}))
    queue_mana_tap_triggers(host, seat, trigger_source, bundle)
    reversible = bool(
        not host.state.pending_trigger_batches
        and not observed
        and not triggered
        and (artifact_selection is None or not artifact_selection.cards)
        and ability.tap_source
        and ability.activation_limit is None
        and not sum(ability.mana.values())
        and not any((mana_payment or {}).values())
        and not ability.choices
        and not any(
            (
                ability.untap_source,
                ability.discard_source,
                ability.sacrifice_source,
                ability.exile_source,
                ability.life_payment,
                ability.energy_payment,
                ability.random_discard_payment,
                ability.loyalty_delta is not None,
                ability.source_counter_removal_cost is not None,
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
    from .conditional_exile_return import conditional_cost_returns_pending
    if conditional_cost_returns_pending():
        return
    if host._stabilize():
        clear_mana_undo_stack(host.state.players[seat].stats)
        return
    host.turn_priority.retain_priority_after_action(seat)


def _mana_plan_modes(
    host: ManaActivationHost,
    seat: str,
    card: Any,
) -> tuple[ManaMode, ...]:
    record = host.card_record(card)
    if not record:
        abilities = mana_plan_abilities(host, seat, card)
        modes = payable_mana_modes(typed_mana_modes_for_abilities(host, seat, card,
            tuple(ability for ability in abilities
                if host._activation_condition_status(seat, ability, card)[0] == 'payable')))
        if not modes:
            raise GameRuleError(f"{card.ref} has no compiled mana mode")
        return modes
    mana_abilities = mana_plan_abilities(host,seat,card)
    if any(ability.activation_limit is not None for ability in mana_abilities):
        raise GameRuleError(
            f"{card.ref} has a usage-limited mana ability; activate it explicitly"
        )
    payable=tuple(ability for ability in mana_abilities
        if host._activation_condition_status(seat,ability,card)[0]=='payable')
    if mana_abilities and not payable:
        raise GameRuleError(f"{card.printed_name}'s mana ability has an unmet or unresolved activation condition")
    modes = payable_mana_modes(typed_mana_modes_for_abilities(host,seat,card,payable))
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
    tap_source: bool,
    input_payment: ManaStepInput | None = None,
    artifact_ability_id: str | None = None,
    requested_restriction: str | None = None,
    payment_window: bool = False,
) -> tuple[ManaMode, str | None]:
    price_key = input_payment.key if input_payment else None
    matching = [
        value for value in modes
        if value.tap_source == tap_source and value.artifact_hand_mana == artifact_ability_id
        and (artifact_ability_id is not None or normalize_mana_bundle(value.bundle) == bundle)
        and (value.price.key if value.price else None) == price_key
    ]
    has_rider = any(is_source_bound_cast_mana(value.restriction) for value in matching)
    if payment_window:
        matching = [value for value in matching if (value.restriction or None) == requested_restriction]
    elif is_source_bound_cast_mana(requested_restriction) or (has_rider and requested_restriction is not None):
        matching = [value for value in matching if value.restriction == requested_restriction]
    mode = next(iter(matching), None)
    if mode is None:
        raise GameRuleError(
            f"Declared output is not a recognized mana mode of {card.printed_name}"
        )
    compiled = mode.restriction or None
    restriction_allows = bool(
        compiled and host._mana_restriction_allows(compiled, spend_context)
    )
    if payment_window and compiled and not restriction_allows:
        from .mana_restriction_model import valid_produced_mana_spend_restriction

        restriction_allows = valid_produced_mana_spend_restriction(compiled)
    if mode.requires_choice and artifact_ability_id is None:
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


def _commit_plan_input(host, seat, mode, input_payment):
    if mode.price is None:
        return
    price = mode.price
    try:
        _, spent = pay_mana_from_pool(
            host._spendable_mana_pool(seat, price.spend_context), price.option.requirements,
            payment=input_payment.payment, spending_policy=price.spending_policy,
        )
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    host._apply_mana_spend(seat, spent, price.spend_context)


def _capture_plan_chosen_subtype(host, seat, card, mode):
    if mode.price is None:
        return None
    from .public_quantity_mana_runtime import capture_mana_source_information, chosen_subtype_mana_output

    for ability in host._activated_abilities(card):
        if ability.ability_id != mode.price.ability_id:
            continue
        output = chosen_subtype_mana_output(ability)
        if output is not None:
            return output, capture_mana_source_information(host, seat, card, ability)
        break
    return None


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
    triggered_mana_option: str | None = None,
    chosen_subtype_mana=None,
    artifact_selection=None,
) -> None:
    creature_activation = capture_creature_ability_activation(host, seat, card)
    trigger_source = tap_mana_source(host, card, tap_source=mode.tap_source)
    from .mana_source_counter_payment_model import exact_mana_cost_order
    cost_operations = {"sacrifice_source", "exile_hand_source", "remove_source_counters", EXERT_MANA_SOURCE}
    if exact_mana_cost_order(host.state):cost_operations.add("pay_life")
    cost_effects = tuple(
        effect for effect in mode.side_effects
        if effect.get("op") in cost_operations
    )
    result_effects = tuple(
        effect for effect in mode.side_effects
        if effect.get("op") not in cost_operations
    )
    if mode.tap_source:
        set_permanent_tapped(host,card.ref,actor=seat,tapped=True,reason="mana ability cost",log=False)
    apply_mana_mode_effects(
        host,
        seat,
        cost_effects,
        source=card,
        payment_id=payment_id,
        replacement_selections_by_event=replacement_selections_by_event,
    )
    publish_creature_ability_activation(host, creature_activation)
    if chosen_subtype_mana is not None:
        from .public_quantity_mana_runtime import public_quantity_mana_amount

        output, source_information = chosen_subtype_mana
        if sum(bundle.values()) != public_quantity_mana_amount(
            host, seat, card, output, source_information=source_information,
        ):
            raise GameRuleError("The mana plan no longer matches the current chosen subtype count")
    if artifact_selection is not None:
        from .rules.hand_artifact_mana import commit_artifact_hand_mana_reveal

        bundle = commit_artifact_hand_mana_reveal(host, artifact_selection, card.ref)
    from .mana_ability_runtime import tap_mana_production_factor

    factor = tap_mana_production_factor(host, seat, trigger_source)
    bundle = {color: amount * factor for color, amount in bundle.items()}
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
        f"{seat} {'tapped' if mode.tap_source else 'activated'} {card.ref} for {public_bundle}.",
        {"source": card.ref, "bundle": public_bundle},
        importance=0,
        changed_objects=[card.object_id],
        changed_players=[seat],
    )
    commit_triggered_mana(host, seat, trigger_source, bundle,
        **({"selection": triggered_mana_option} if triggered_mana_option is not None else {}))
    queue_mana_tap_triggers(host, seat, trigger_source, bundle)


def complete_mana_plan_activations(
    host: ManaActivationHost,
    seat: str,
    activations: Sequence[Mapping[str, Any]],
    *,
    spend_context: str | None = None,
    payment_id: str | None = None,
    replacement_selections_by_event: Mapping[str, Any] | None = None,
    preserve_source_id: str | None = None,
    payment_window: bool = False,
) -> None:
    """Validate and commit an authoritative derived mana-payment plan."""

    for activation in activations:
        tap_source=activation.get('tap_source',True)
        if type(tap_source) is not bool:
            raise GameRuleError('A mana plan tap cost must be Boolean')
        card = host._resolve_object(
            seat,
            str(activation["source"]),
            zones={"battlefield"} if tap_source else {"battlefield", "hand"},
            controlled_only=True,
        )
        if card.zone == "hand" and activation["source"] != card.ref:
            raise GameRuleError("Hand mana steps require the exact source reference")
        if card.tapped and tap_source:
            raise GameRuleError(f"{card.ref} is already tapped")
        data = host._effective_card_data(card)
        card_types, _subtypes, supertypes = host._type_parts(
            str(data.get("type_line") or "")
        )
        if (
            tap_source and "creature" in card_types
            and summoning_sickness_prohibits_tap_or_untap_cost(
                host,
                card,
                as_though_haste=host._may_activate_creature_as_haste(
                    seat, card
                ),
            )
        ):
            raise GameRuleError(f"{card.ref} is summoning sick")
        from .rules.hand_artifact_mana import prepare_artifact_hand_mana_step

        artifact_ability_id, artifact_selection = prepare_artifact_hand_mana_step(host, seat, card, activation)
        bundle = normalize_mana_bundle(
            {"C": artifact_selection.colorless} if artifact_selection is not None else activation.get("bundle")
        )
        modes = _mana_plan_modes(host, seat, card)
        if card.object_id == preserve_source_id:
            from .mana import mana_modes_preserving_source

            modes = mana_modes_preserving_source(modes)
        try:
            input_payment = ManaStepInput.from_dict(activation["input"]) if "input" in activation else None
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        mode, restriction = _selected_plan_mode(
            host,
            seat,
            card,
            modes,
            bundle,
            allow_conditional=bool(activation.get("allow_conditional")),
            spend_context=spend_context,
            tap_source=tap_source,
            input_payment=input_payment,
            artifact_ability_id=artifact_ability_id,
            requested_restriction=activation.get("restriction"),
            payment_window=payment_window,
        )
        chosen_subtype_mana = _capture_plan_chosen_subtype(host, seat, card, mode)
        _commit_plan_input(host, seat, mode, input_payment)
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
            chosen_subtype_mana=chosen_subtype_mana,
            triggered_mana_option=activation.get(TRIGGERED_MANA_OPTION_FIELD),
            artifact_selection=artifact_selection,
        )


__all__ = [
    "ManaActivationHost",
    "complete_mana_activation",
    "complete_mana_plan_activations",
]
