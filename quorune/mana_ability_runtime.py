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
from .mana import ManaMode
from .object_query import object_query_result, query_objects
from .util import normalize_mana_bundle
from .public_quantity_mana_runtime import (
    public_quantity_output, public_quantity_mana_modes, select_public_quantity_mana,
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


def _color_set_mana_modes(
    host: ManaAbilityRuntimeHost,
    seat: str,
    ability: ActivatedAbility,
) -> tuple[ManaMode, ...]:
    spec = ability.color_set_mana_output
    if spec is None:
        return ()
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
        if not colors:
            return (ManaMode(normalize_mana_bundle(None)),)
        return tuple(
            ManaMode(normalize_mana_bundle({color: 1}))
            for color in colors
        )
    raise GameRuleError("Unsupported typed color-set mana selection")


def mana_modes_for_ability(
    host: ManaAbilityRuntimeHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
) -> tuple[ManaMode, ...]:
    """Return the output modes for one selected activated mana ability."""

    side_effects: list[dict[str, Any]] = []
    if ability.sacrifice_source:
        side_effects.append({"op": "sacrifice_source"})
    if ability.life_payment:
        side_effects.append(
            {"op": "pay_life", "amount": ability.life_payment}
        )
    side_effects.extend(_mana_result_effects(ability))

    def decorated(modes: tuple[ManaMode, ...]) -> tuple[ManaMode, ...]:
        return tuple(
            ManaMode(
                mode.bundle,
                conditional=mode.conditional,
                restriction=(
                    ability.mana_spend_restriction
                    or mode.restriction
                ),
                side_effects=tuple(side_effects) or mode.side_effects,
                requires_choice=bool(ability.choices) or mode.requires_choice,
            )
            for mode in modes
        )

    if ability.color_set_mana_output is not None:
        return decorated(_color_set_mana_modes(host, seat, ability))
    if public_quantity_output(ability) is not None:
        return decorated(public_quantity_mana_modes(host, seat, source, ability))
    if ability.fixed_mana_outputs:
        fixed_outputs = tuple(ability.fixed_mana_outputs)
        if all(
            sum(1 for amount in mode.bundle.values() if amount) == 1
            for mode in fixed_outputs
        ):
            color_order = {color: index for index, color in enumerate("WUBRGC")}
            fixed_outputs = tuple(
                sorted(
                    fixed_outputs,
                    key=lambda mode: min(
                        color_order[color]
                        for color, amount in mode.bundle.items()
                        if amount
                    ),
                )
            )
        return decorated(
            tuple(ManaMode(mode.bundle) for mode in fixed_outputs)
        )
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


def _land_output_colors(
    host: ManaAbilityRuntimeHost,
) -> dict[str, set[str]]:
    """Compute land-produced colors to a deterministic least fixed point."""

    colors_by_object: dict[str, set[str]] = {}
    dynamic_by_object: dict[str, str] = {}
    controller_by_object: dict[str, str] = {}
    for seat in host.active_seats:
        for object_id in host.state.players[seat].zones["battlefield"]:
            land = host.state.cards[object_id]
            if land.controller != seat or land.phased_out:
                continue
            data = host._effective_card_data(land)
            card_types, _, _ = host._type_parts(
                str(data.get("type_line") or "")
            )
            if "land" not in card_types:
                continue
            colors = colors_by_object.setdefault(object_id, set())
            controller_by_object[object_id] = seat
            for ability in host._activated_abilities(land):
                if not ability.mana_ability or land.zone not in ability.zones:
                    continue
                if ability.fixed_mana_outputs:
                    colors.update(
                        color
                        for mode in ability.fixed_mana_outputs
                        for color, amount in mode.bundle.items()
                        if color in "WUBRG" and amount
                    )
                elif ability.color_set_mana_output is not None:
                    colors.update(
                        color
                        for mode in _color_set_mana_modes(host, seat, ability)
                        for color, amount in mode.bundle.items()
                        if color in "WUBRG" and amount
                    )
                elif public_quantity_output(ability) is not None:
                    colors.update(color for color in public_quantity_output(ability).colors if color in 'WUBRG')
                elif ability.dynamic_mana_output is not None:
                    dynamic_by_object[object_id] = ability.dynamic_mana_output
    changed = True
    while changed:
        changed = False
        for object_id, dynamic in sorted(dynamic_by_object.items()):
            if dynamic != "opponent_land_colors":
                continue
            controller = controller_by_object[object_id]
            produced = {
                color
                for other_id, other_colors in colors_by_object.items()
                if controller_by_object[other_id] != controller
                for color in other_colors
            }
            before = len(colors_by_object[object_id])
            colors_by_object[object_id].update(produced)
            changed = changed or len(colors_by_object[object_id]) != before
    return colors_by_object


def _opponent_land_colors(
    host: ManaAbilityRuntimeHost,
    seat: str,
) -> tuple[str, ...]:
    colors_by_object = _land_output_colors(host)
    colors = {
        color
        for object_id, produced in colors_by_object.items()
        if host.state.cards[object_id].controller != seat
        for color in produced
    }
    return tuple(color for color in "WUBRG" if color in colors)


def typed_mana_modes_for_abilities(
    host: ManaAbilityRuntimeHost,
    seat: str,
    source: Any,
    abilities: tuple[ActivatedAbility, ...] | list[ActivatedAbility],
) -> tuple[ManaMode, ...]:
    """Return deduplicated modes owned by typed mana descriptors."""

    result: dict[tuple[tuple[str, int], ...], ManaMode] = {}
    for ability in abilities:
        if not ability.mana_ability:
            continue
        for mode in mana_modes_for_ability(host, seat, source, ability):
            key = tuple(
                (color, int(mode.bundle.get(color, 0)))
                for color in "WUBRGC"
                if int(mode.bundle.get(color, 0))
            )
            result.setdefault(key, mode)
    return tuple(result.values())


def payable_mana_modes(
    *groups: tuple[ManaMode, ...] | list[ManaMode],
) -> tuple[ManaMode, ...]:
    """Merge source modes canonically, excluding outputs that cannot pay."""

    result: dict[tuple[tuple[str, int], ...], ManaMode] = {}
    for mode in (mode for group in groups for mode in group):
        key = tuple(
            (color, int(mode.bundle.get(color, 0)))
            for color in "WUBRGC"
            if int(mode.bundle.get(color, 0))
        )
        if key:
            result.setdefault(key, mode)
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

    if public_quantity_output(ability) is not None:
        return select_public_quantity_mana(host, seat, source, ability, response,
            source_information=source_information)
    legal_modes = mana_modes_for_ability(host, seat, source, ability)
    declared = normalize_mana_bundle(response.get("mana_output"))
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
    "mana_modes_for_ability",
    "mana_output_for_ability",
    "payable_mana_modes",
    "typed_mana_modes_for_abilities",
]
