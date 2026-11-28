from __future__ import annotations

"""Runtime output selection for compiler-pinned activated mana abilities."""

from dataclasses import replace
import re
from typing import Any, Mapping, Protocol

from .abilities import ActivatedAbility
from .color_set_mana_abilities import (
    ColorSetRelation,
    ColorSetSelection,
)
from .errors import GameRuleError
from .land_mana_potential import LandManaPotential, land_mana_potential_output
from .land_play_mana_model import land_play_mana_output
from .mana import ManaMode, ManaModePrice
from .mana_spend_rules import (
    CommanderEntryCounterMana, CommanderScryMana, bind_mana_spend_rule, is_source_bound_cast_mana,
)
from .object_query import object_query_result, query_objects
from .turn_history import land_played_this_turn
from .util import normalize_mana_bundle
from .chosen_color_mana_runtime import chosen_color_mana_output, chosen_color_mana_modes
from .public_quantity_mana_runtime import (
    chosen_subtype_mana_output, public_quantity_output, public_quantity_mana_modes, select_public_quantity_mana,
)


class ManaAbilityRuntimeHost(Protocol):
    active_seats: list[str]
    state: Any

    def card_record(self, card: Any) -> Any: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _commander_identity(self, seat: str) -> set[str]: ...

    def _activated_abilities(
        self, card: Any
    ) -> tuple[ActivatedAbility, ...]: ...


_DAMAGE_CONTROLLER_RESULT = re.compile(
    r"^builtin:mana-result-damage-controller:(?P<amount>[1-9][0-9]*)$"
)


def _mana_result_effects(
    ability: ActivatedAbility,
) -> tuple[dict[str, Any], ...]:
    if ability.fixed_mana_result is not None:
        from .rules.whole_hand_mana import is_whole_hand_mana_ability

        if is_whole_hand_mana_ability(ability):
            return ()
        return (ability.fixed_mana_result.mode_effect(),)
    key = ability.builtin_semantic_key or ""
    match = _DAMAGE_CONTROLLER_RESULT.fullmatch(key)
    if match is None:
        return ()
    return (
        {
            "op": "damage_self",
            "amount": int(match.group("amount")),
        },
    )


def _one_mana_color_modes(colors) -> tuple[ManaMode, ...]:
    modes = tuple(ManaMode(normalize_mana_bundle({color: 1})) for color in "WUBRG" if color in colors)
    return modes or (ManaMode(normalize_mana_bundle(None)),)


def _color_set_mana_modes(
    host: ManaAbilityRuntimeHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
) -> tuple[ManaMode, ...]:
    spec = ability.color_set_mana_output
    if spec is None:
        return ()
    if spec.uses_commander_identity:
        return _one_mana_color_modes(host._commander_identity(seat))
    if spec.linked_exile_pair is not None:
        from .continuous_effect_model import ContinuousObjectIdentity
        from .linked_ability_exile_state import current_linked_ability_exile

        identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
        linked = current_linked_ability_exile(host, source=identity, ability_pair=spec.linked_exile_pair)
        current = (host.state.cards[entry.object_id] for entry in linked)
        colors = {color for card in current if not card.face_down
                  for color in host._effective_card_data(card).get("colors", ())}
        modes = _one_mana_color_modes(colors)
        if not modes and spec.relation is ColorSetRelation.LINKED_GRAVEYARD_EXILE:
            return (ManaMode(normalize_mana_bundle(None)),)
        return modes
    query = replace(
        spec.query,
        controller=(
            seat if spec.relation is ColorSetRelation.CONTROLLER else None
        ),
        owner=(seat if spec.relation is ColorSetRelation.OWNER else None),
    )
    rows = []
    for card in host.state.cards.values():
        if card.zone not in spec.query.zones:
            continue
        if (
            spec.relation is ColorSetRelation.CONTROLLER
            and card.controller != seat
        ):
            continue
        if spec.relation is ColorSetRelation.OWNER and card.owner != seat:
            continue
        effective = host._effective_card_data(card)
        rows.append(
            object_query_result(
                card,
                effective,
                type_parts=host._type_parts(
                    str(effective.get("type_line") or "")
                ),
                known_to_actor=True,
                attached_to_ref=None,
            )
        )
    matches = query_objects(rows, query)
    colors = tuple(
        color
        for color in "WUBRG"
        if any(color in row.colors for row in matches)
    )
    if spec.selection is ColorSetSelection.ONE_EACH:
        return (
            ManaMode(
                normalize_mana_bundle({color: 1 for color in colors})
            ),
        )
    if spec.selection is ColorSetSelection.CHOOSE_ONE:
        return _one_mana_color_modes(colors)
    raise GameRuleError("Unsupported typed color-set mana selection")


def _fixed_output_modes(ability, source):
    from .fixed_mana_abilities import LUCK_COUNTER_MANA_OUTPUT

    if ability.dynamic_mana_output == LUCK_COUNTER_MANA_OUTPUT and source.counters.get("luck", 0) > 0:
        return _one_mana_color_modes("WUBRG")
    fixed_outputs = tuple(ability.fixed_mana_outputs)
    if all(sum(1 for amount in mode.bundle.values() if amount) == 1 for mode in fixed_outputs):
        color_order = {color: index for index, color in enumerate('WUBRGC')}
        fixed_outputs = tuple(sorted(fixed_outputs, key=lambda mode: min(
            color_order[color] for color, amount in mode.bundle.items() if amount)))
    return tuple(ManaMode(mode.bundle) for mode in fixed_outputs)


def activated_mana_spending_rule(ability: ActivatedAbility, source: Any, controller: str) -> str | None:
    spec = ability.color_set_mana_output
    if spec is not None and spec.commander_type_scry:
        return CommanderScryMana(controller, source.object_id, source.logical_object_id).encode()
    if spec is not None and spec.commander_entry_counters:
        return CommanderEntryCounterMana(controller, source.object_id, source.logical_object_id).encode()
    return bind_mana_spend_rule(ability.mana_spend_restriction, source)


def mana_modes_for_ability(
    host: ManaAbilityRuntimeHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
) -> tuple[ManaMode, ...]:
    """Return the output modes for one selected activated mana ability."""
    from .fixed_mana_results import doubles_unspent_mana

    if doubles_unspent_mana(ability):
        return (ManaMode(normalize_mana_bundle(None), double_unspent=True),)

    from .artifact_hand_mana_model import is_artifact_hand_mana
    if is_artifact_hand_mana(ability):
        from .rules.hand_artifact_mana import current_artifact_hand_cards

        potential = normalize_mana_bundle({"C": 2 * len(current_artifact_hand_cards(host, seat))})
        return (ManaMode(potential, requires_choice=True, artifact_hand_mana=ability.ability_id),)
    side_effects: list[dict[str, Any]] = []
    if ability.sacrifice_source:
        side_effects.append({"op": "sacrifice_source"})
    if ability.zones == ("hand",) and ability.exile_source and ability.fixed_mana_outputs:
        side_effects.append({"op": "exile_hand_source"})
    if ability.life_payment:
        side_effects.append(
            {"op": "pay_life", "amount": ability.life_payment}
        )
    from .mana_source_counter_payment_model import exact_mana_source_counter_payment
    counter_cost=ability.source_counter_removal_cost
    if exact_mana_source_counter_payment(host.state) and counter_cost is not None and counter_cost.schema_version==1:
        side_effects.append({'op':'remove_source_counters','cost':counter_cost.to_dict()})
    side_effects.extend(_mana_result_effects(ability))
    restriction = activated_mana_spending_rule(ability, source, seat)

    def decorated(modes: tuple[ManaMode, ...]) -> tuple[ManaMode, ...]:
        return tuple(
            ManaMode(
                mode.bundle,
                conditional=mode.conditional,
                restriction=restriction or mode.restriction,
                side_effects=tuple(side_effects) or mode.side_effects,
                requires_choice=bool(ability.choices) or mode.requires_choice,
                tap_source=ability.tap_source,
            )
            for mode in modes
        )

    if ability.color_set_mana_output is not None:
        return decorated(_color_set_mana_modes(host, seat, source, ability))
    if public_quantity_output(ability) is not None:
        return decorated(public_quantity_mana_modes(host, seat, source, ability))
    if land_play := land_play_mana_output(ability):
        played = land_played_this_turn(host.state, seat)
        if played is None:
            return ()
        color = land_play.played_color if played else land_play.normal_color
        return decorated((ManaMode(normalize_mana_bundle({color: 1})),))
    if ability.fixed_mana_outputs:
        return decorated(_fixed_output_modes(ability, source))
    if chosen_color_mana_output(ability) is not None:
        return decorated(chosen_color_mana_modes(source, ability))
    potential = land_mana_potential_output(ability)
    if potential is not None:
        outputs = _land_potential_types(host, seat, potential)
        amount = potential.amount(source.counters)
        modes = tuple(ManaMode(normalize_mana_bundle({color: amount})) for color in outputs)
        return decorated(modes or (ManaMode(normalize_mana_bundle(None)),))
    if ability.dynamic_mana_output == "opponent_land_colors":
        return decorated(tuple(
            ManaMode(
                {
                    **normalize_mana_bundle(None),
                    color: 1,
                },
            )
            for color in _opponent_land_colors(host, seat)
        ))
    return ()


def _land_ability_colors(host, seat, land, ability):
    """Return the legacy colored-land contribution for one ability."""
    if ability.fixed_mana_outputs:
        modes = _fixed_output_modes(ability, land)
    elif ability.color_set_mana_output is not None:
        modes = _color_set_mana_modes(host, seat, land, ability)
    elif public_quantity_output(ability) is not None:
        return {color for color in public_quantity_output(ability).colors if color in 'WUBRG'}
    elif chosen_color_mana_output(ability) is not None:
        modes = chosen_color_mana_modes(land, ability)
    elif land_play_mana_output(ability) is not None:
        modes = mana_modes_for_ability(host, seat, land, ability)
    else:
        return set()
    return {color for mode in modes for color, amount in mode.bundle.items() if color in 'WUBRG' and amount}

def _independent_land_mana_types(host, seat, land, ability, *, legacy):
    if legacy:
        return _land_ability_colors(host, seat, land, ability)
    quantity = public_quantity_output(ability)
    if quantity is not None:
        from .public_quantity_mana_model import CHOSEN_DEVOTION_MANA_VERSION

        if quantity.schema_version == CHOSEN_DEVOTION_MANA_VERSION:
            from .devotion import current_devotion

            return {color for color in quantity.colors if current_devotion(host, seat, (color,))}
    modes = mana_modes_for_ability(host, seat, land, ability)
    return {color for mode in modes for color, amount in mode.bundle.items() if amount}


def _land_mana_types_by_controller(host: ManaAbilityRuntimeHost, *, legacy: bool) -> dict[str, set[str]]:
    """Read positive outputs ignoring prices, then propagate across opponent mirrors."""
    outputs = {seat: set() for seat in host.active_seats}
    opponent_mirrors = set()
    for seat, types in outputs.items():
        for object_id in host.state.players[seat].zones["battlefield"]:
            land = host.state.cards[object_id]
            if land.controller != seat or land.phased_out:
                continue
            data = host._effective_card_data(land)
            if "land" not in host._type_parts(str(data.get("type_line") or ""))[0]:
                continue
            for ability in host._activated_abilities(land):
                if not ability.mana_ability or land.zone not in ability.zones:
                    continue
                output = land_mana_potential_output(ability)
                if ability.dynamic_mana_output == "opponent_land_colors" or output is LandManaPotential.OPPONENT_COLORS:
                    opponent_mirrors.add(seat)
                elif output is None:
                    types.update(_independent_land_mana_types(host, seat, land, ability, legacy=legacy))
    # Pool takes the union already available under its own controller. Only an
    # opponent mirror can add a type to that controller's union.
    changed = True
    while changed:
        changed = False
        for seat in sorted(opponent_mirrors):
            opponent_colors = {color for other, types in outputs.items()
                               if other != seat for color in types if color != "C"}
            if not opponent_colors.issubset(outputs[seat]):
                outputs[seat].update(opponent_colors)
                changed = True
    return outputs


def _land_potential_types(host: ManaAbilityRuntimeHost, seat: str, potential: LandManaPotential,
                          *, legacy: bool = False) -> tuple[str, ...]:
    outputs = _land_mana_types_by_controller(host, legacy=legacy)
    if potential is not LandManaPotential.OPPONENT_COLORS:
        types = outputs.get(seat, set())
    else:
        types = {color for other, candidate_types in outputs.items() if other != seat for color in candidate_types}
    return tuple(color for color in potential.types if color in types)


def _opponent_land_colors(host: ManaAbilityRuntimeHost, seat: str) -> tuple[str, ...]:
    return _land_potential_types(host, seat, LandManaPotential.OPPONENT_COLORS, legacy=True)


def _select_land_potential_mana(host, seat, potential, response, *, amount):
    types = _land_potential_types(host, seat, potential)
    choice = response.get("mana_choice")
    bundle = response.get("mana_output")
    if choice is not None:
        if bundle is not None or type(choice) is not str or choice not in types:
            raise GameRuleError("Choose one currently available land mana type")
        return normalize_mana_bundle({choice: amount})
    if bundle is None:
        if len(types) > 1:
            raise GameRuleError("Choose which mana this ability produces")
        return normalize_mana_bundle({types[0]: amount} if types else None)
    if not isinstance(bundle, Mapping) or any(
        type(color) is not str or color not in "WUBRGC" or len(color) != 1
        or type(value) is not int or value not in {0, amount} for color, value in bundle.items()
    ):
        raise GameRuleError("Select an exact single mana output")
    selected = tuple(color for color, value in bundle.items() if value)
    if (not types and not selected) or (len(selected) == 1 and selected[0] in types):
        return normalize_mana_bundle(bundle)
    raise GameRuleError("Declared mana output is not a currently legal mana mode")


def _current_tap_mana_multipliers(host, seat, source, *, current_source):
    from .semantic_runtime.action_permissions import controller_action_permissions
    from .semantic_runtime.mana_spending import (
        TAP_MANA_PRODUCTION_MULTIPLIERS, has_tap_mana_multiplier_program,
    )

    if source is None or not has_tap_mana_multiplier_program(host):
        return ()
    types, supertypes = source.types, source.supertypes
    if current_source:
        current = host.state.cards.get(source.object_id)
        if (current is not None and current.zone == "battlefield" and not current.phased_out
                and current.logical_object_id == source.logical_object_id):
            types, _, supertypes = host._type_parts(str(host._effective_card_data(current).get("type_line") or ""))
    is_basic_land = "land" in types and "basic" in supertypes
    providers = {card.logical_object_id: card.object_id for card in host.state.cards.values()
                 if card.zone == "battlefield" and card.controller == seat and not card.phased_out}
    return tuple(
        (providers[permission.source_logical_object_id], factor)
        for permission in controller_action_permissions(host, seat)
        if permission.kind in TAP_MANA_PRODUCTION_MULTIPLIERS
        for _, factor, basics_only in (TAP_MANA_PRODUCTION_MULTIPLIERS[permission.kind],)
        if (not basics_only or is_basic_land) and permission.source_logical_object_id in providers
    )


def tap_mana_production_factor(host, seat, source):
    from math import prod

    return prod(factor for _, factor in _current_tap_mana_multipliers(host, seat, source, current_source=True))


def typed_mana_modes_for_abilities(
    host: ManaAbilityRuntimeHost,
    seat: str,
    source: Any,
    abilities: tuple[ActivatedAbility, ...] | list[ActivatedAbility],
) -> tuple[ManaMode, ...]:
    """Return deduplicated modes owned by typed mana descriptors."""

    from .semantic_runtime.mana_spending import has_tap_mana_multiplier_program
    from .triggered_mana import tap_mana_source

    result = {}
    source_snapshot = (tap_mana_source(host, source, tap_source=True)
                       if has_tap_mana_multiplier_program(host) else None)
    multipliers = _current_tap_mana_multipliers(host, seat, source_snapshot, current_source=False)
    for ability in abilities:
        if not ability.mana_ability:
            continue
        modes = mana_modes_for_ability(host, seat, source, ability)
        if ability.tap_source and multipliers:
            surviving = tuple((provider, factor) for provider, factor in multipliers
                              if not ability.sacrifice_source or provider != source.object_id)
            modes = tuple(replace(mode, tap_mana_multipliers=surviving) for mode in modes)
        if ability.mana_cost_options or has_ordinary_mana_mode_price(ability):
            modes = _priced_mana_modes(host, seat, source, ability, modes)
        for mode in modes:
            key = tuple(
                (color, int(mode.bundle.get(color, 0)))
                for color in "WUBRGC"
                if int(mode.bundle.get(color, 0))
            )
            result.setdefault((key, mode.tap_source, mode.price.key if mode.price else None,
                               mode.tap_mana_multipliers, mode.artifact_hand_mana,
                               mode.restriction if is_source_bound_cast_mana(mode.restriction) else None), mode)
    return tuple(result.values())


def has_ordinary_mana_mode_price(ability: ActivatedAbility) -> bool:
    from .fixed_mana_abilities import supports_paid_five_color_combination
    from .rules.exert_mana import is_exert_mana
    from .fixed_mana_results import doubles_unspent_mana
    from .rules.next_cast_library_access import NEXT_CAST_LIBRARY_ACCESS

    if (is_exert_mana(ability)
            or doubles_unspent_mana(ability)
            or supports_paid_five_color_combination(ability)
            or ability.fixed_mana_result is not None and ability.fixed_mana_result.kind == NEXT_CAST_LIBRARY_ACCESS):
        return True
    spec = ability.color_set_mana_output
    return (chosen_subtype_mana_output(ability) is not None
            or (spec is not None and spec.commander_entry_counters))


def _priced_mana_modes(host, seat, source, ability, modes):
    from .activation_mana_cost import ActivationManaCostOption
    from .fixed_mana_abilities import supports_hybrid_filter_price
    from .mana_restrictions import ability_mana_spend_context
    from .rules.activation.pricing import ordinary_activation_prices, targetless_activation_prices
    from .semantic_runtime.mana_spending import current_mana_spending_policy

    if has_ordinary_mana_mode_price(ability):
        prices, _, _ = ordinary_activation_prices(host, seat, source, ability, targets=())
        options = tuple(ActivationManaCostOption(price.order, price.requirements) for price in prices)
    elif supports_hybrid_filter_price(ability, ability.mana_cost_options, ability.fixed_mana_outputs):
        options = targetless_activation_prices(host, seat, source, ability)
    else:
        return ()
    context = ability_mana_spend_context(str(host._effective_card_data(source).get("type_line") or ""), source_zone=source.zone)
    policy = current_mana_spending_policy(host, seat, context)
    return tuple(
        replace(mode, price=ManaModePrice(ability.ability_id, option, context, policy))
        if any(option.requirements.values()) else mode
        for mode in modes for option in options
    )


def payable_mana_modes(
    *groups: tuple[ManaMode, ...] | list[ManaMode],
) -> tuple[ManaMode, ...]:
    """Merge source modes canonically, excluding outputs that cannot pay."""

    result = {}
    for mode in (mode for group in groups for mode in group):
        key = tuple(
            (color, int(mode.bundle.get(color, 0)))
            for color in "WUBRGC"
            if int(mode.bundle.get(color, 0))
        )
        if key or mode.artifact_hand_mana is not None or mode.double_unspent:
            result.setdefault((key, mode.price.key if mode.price else None, mode.tap_mana_multipliers,
                               mode.artifact_hand_mana,
                               mode.restriction if is_source_bound_cast_mana(mode.restriction) else None), mode)
    return tuple(result.values())


def mana_output_for_ability(
    host: ManaAbilityRuntimeHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
    response: Mapping[str, Any],
    *,
    source_information=None,
) -> dict[str, int]:
    """Validate the submitted output against the advertised mode set."""
    from .fixed_mana_results import doubles_unspent_mana

    if doubles_unspent_mana(ability):
        output = normalize_mana_bundle(host.state.players[seat].mana_pool)
        if ("mana_output" in response and normalize_mana_bundle(response["mana_output"]) != output
                or response.get("mana_choice")):
            raise GameRuleError("Mana doubling output must match the unspent pool after payment")
        return output

    if public_quantity_output(ability) is not None:
        return select_public_quantity_mana(host, seat, source, ability, response,
            source_information=source_information)
    potential = land_mana_potential_output(ability)
    if potential is not None:
        return _select_land_potential_mana(host, seat, potential, response,
                                           amount=potential.amount(source.counters))
    spec = ability.color_set_mana_output
    if spec is not None and spec.relation is ColorSetRelation.LINKED_GRAVEYARD_EXILE and "mana_choice" in response:
        choice = response["mana_choice"]
        if type(choice) is not str or choice.upper() not in {"W", "U", "B", "R", "G"}:
            raise GameRuleError("Choose a current linked-exile color")
    legal_modes = (chosen_color_mana_modes(source, ability, source_information=source_information)
                   if chosen_color_mana_output(ability) is not None else mana_modes_for_ability(host, seat, source, ability))
    raw_output = response.get("mana_output")
    if raw_output is not None:
        from .fixed_mana_abilities import supports_paid_five_color_combination

        if supports_paid_five_color_combination(ability) and (
            not isinstance(raw_output, Mapping)
            or any(type(amount) is not int for amount in raw_output.values())
        ):
            raise GameRuleError("A five-color combination requires integer mana quantities")
    declared = normalize_mana_bundle(raw_output)
    raw_choice = str(response.get("mana_choice") or "").upper()
    if raw_choice in "WUBRGC" and len(raw_choice) == 1:
        declared[raw_choice] += 1
    if legal_modes:
        if sum(declared.values()):
            if not any(
                normalize_mana_bundle(mode.bundle) == declared
                for mode in legal_modes
            ):
                raise GameRuleError(
                    "Declared mana output is not a currently legal mana mode"
                )
            return declared
        if len(legal_modes) == 1:
            return normalize_mana_bundle(legal_modes[0].bundle)
        raise GameRuleError("Choose which mana this ability produces")

    raise GameRuleError("Mana ability has no compiler-pinned output descriptor")


__all__ = [
    "ManaAbilityRuntimeHost",
    "has_ordinary_mana_mode_price",
    "mana_modes_for_ability",
    "mana_output_for_ability",
    "payable_mana_modes",
    "typed_mana_modes_for_abilities",
]
