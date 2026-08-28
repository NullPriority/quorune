from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectError,
    ContinuousEffectOrigin,
    ContinuousEffectRelation,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from .object_predicate import ObjectQuerySpec
from .object_query import ObjectQueryResult, object_matches_query
from .ability_fragments import (
    StaticAbilityFragment,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
    canonical_ability_fragments,
)
from .abilities import ActivatedAbility
from .replacement.immutable import thaw_value


_SUBLAYER_ORDER = {
    (Layer.COPY, "1a"): 0,
    (Layer.COPY, "1b"): 1,
    (Layer.POWER_TOUGHNESS, "7a"): 0,
    (Layer.POWER_TOUGHNESS, "7b"): 1,
    (Layer.POWER_TOUGHNESS, "7c"): 2,
    (Layer.POWER_TOUGHNESS, "7d"): 3,
}


@dataclass(slots=True)
class CharacteristicState:
    name: str
    controller: str | None = None
    mana_cost: str = ""
    mana_value: float = 0.0
    text: str = ""
    executable_text: str = ""
    supertypes: set[str] = field(default_factory=set)
    card_types: set[str] = field(default_factory=set)
    subtypes: set[str] = field(default_factory=set)
    colors: set[str] = field(default_factory=set)
    abilities: list[str] = field(default_factory=list)
    ability_fragments: list[StaticAbilityFragment] = field(
        default_factory=list
    )
    activated_abilities: list[ActivatedAbility] = field(
        default_factory=list
    )
    power: int | None = None
    toughness: int | None = None
    loyalty: int | None = None
    defense: int | None = None
    copiable_values: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "controller": self.controller,
            "mana_cost": self.mana_cost,
            "mana_value": self.mana_value,
            "text": self.text,
            "executable_text": self.executable_text,
            "supertypes": sorted(self.supertypes),
            "card_types": sorted(self.card_types),
            "subtypes": sorted(self.subtypes),
            "colors": sorted(self.colors),
            "abilities": list(self.abilities),
            "ability_fragments": [
                ability_fragment_to_dict(value)
                for value in canonical_ability_fragments(
                    self.ability_fragments
                )
            ],
            "activated_abilities": [
                ability.to_dict() for ability in self.activated_abilities
            ],
            "power": self.power,
            "toughness": self.toughness,
            "loyalty": self.loyalty,
            "defense": self.defense,
            "copiable_values": dict(self.copiable_values),
        }


@dataclass(frozen=True, slots=True)
class ContinuousEvaluation:
    characteristics: Mapping[str, Any]
    applied_effects: tuple[str, ...]
    dependency_cycles: tuple[tuple[str, ...], ...]
    inapplicable_effects: tuple[str, ...]


def _matches(
    condition: ObjectQuerySpec,
    state: CharacteristicState,
    context: Mapping[str, Any],
) -> bool:
    row = ObjectQueryResult(
        object_id=str(context.get("object_id") or "unrepresented"),
        ref=str(context.get("ref") or "unrepresented"),
        printed_name=state.name,
        owner=str(context.get("owner") or state.controller or "unrepresented"),
        controller=str(state.controller or context.get("controller") or ""),
        zone=str(context.get("zone") or "battlefield"),
        types=tuple(sorted(value.casefold() for value in state.card_types)),
        subtypes=tuple(sorted(value.casefold() for value in state.subtypes)),
        supertypes=tuple(
            sorted(value.casefold() for value in state.supertypes)
        ),
        colors=tuple(sorted(value.upper() for value in state.colors)),
        keywords=tuple(
            sorted(value.casefold() for value in state.abilities)
        ),
        mana_value=int(state.mana_value),
        token=bool(context.get("token", False)),
        tapped=bool(context.get("tapped", False)),
        phased_out=bool(context.get("phased_out", False)),
        known_to_actor=bool(context.get("known_to_actor", True)),
    )
    return object_matches_query(row, condition)


def _dependency_order(
    effects: Sequence[ContinuousEffect],
) -> tuple[list[ContinuousEffect], list[tuple[str, ...]]]:
    """Order one layer/sublayer by CDA, dependency, then timestamp.

    Cyclic dependency components fall back to timestamp order, matching the
    CR rule that dependencies inside the loop do not determine their order.
    """

    by_id = {effect.effect_id: effect for effect in effects}
    remaining = set(by_id)
    ordered: list[ContinuousEffect] = []
    cycles: list[tuple[str, ...]] = []
    while remaining:
        ready = [
            by_id[effect_id]
            for effect_id in remaining
            if not (
                set(by_id[effect_id].depends_on).intersection(remaining)
            )
        ]
        if ready:
            ready.sort(
                key=lambda effect: (
                    not effect.characteristic_defining,
                    effect.timestamp,
                    effect.effect_id,
                )
            )
            for effect in ready:
                ordered.append(effect)
                remaining.remove(effect.effect_id)
            continue
        cycle = tuple(
            sorted(
                remaining,
                key=lambda effect_id: (
                    not by_id[effect_id].characteristic_defining,
                    by_id[effect_id].timestamp,
                    effect_id,
                ),
            )
        )
        cycles.append(cycle)
        ordered.extend(by_id[effect_id] for effect_id in cycle)
        remaining.clear()
    return ordered, cycles


def order_continuous_effects(
    effects: Iterable[ContinuousEffect],
) -> tuple[list[ContinuousEffect], list[tuple[str, ...]]]:
    values = tuple(effects)
    effect_ids = [effect.effect_id for effect in values]
    if len(set(effect_ids)) != len(effect_ids):
        raise ContinuousEffectError(
            "Continuous effect IDs must be unique during evaluation"
        )
    groups: dict[tuple[int, int], list[ContinuousEffect]] = {}
    for effect in values:
        sublayer_rank = _SUBLAYER_ORDER.get(
            (effect.layer, effect.sublayer),
            0,
        )
        groups.setdefault(
            (int(effect.layer), sublayer_rank), []
        ).append(effect)
    ordered: list[ContinuousEffect] = []
    cycles: list[tuple[str, ...]] = []
    for key in sorted(groups):
        group, group_cycles = _dependency_order(groups[key])
        ordered.extend(group)
        cycles.extend(group_cycles)
    return ordered, cycles


def _as_words(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return {str(item) for item in value}


def _apply_copy_values(
    state: CharacteristicState,
    value: Any,
) -> None:
    if not isinstance(value, Mapping):
        raise ContinuousEffectError("copy_values requires an object")
    has_explicit_executable_text = "executable_text" in value
    for key, replacement in value.items():
        if key == "text":
            state.text = str(replacement)
            if not has_explicit_executable_text:
                state.executable_text = state.text
        elif key in {"supertypes", "card_types", "subtypes", "colors"}:
            setattr(state, key, _as_words(replacement))
        elif key == "abilities":
            state.abilities = [str(item) for item in replacement]
        elif key == "ability_fragments":
            state.ability_fragments = list(
                canonical_ability_fragments(replacement)
            )
        elif key == "activated_abilities":
            if not isinstance(replacement, (list, tuple)):
                raise ContinuousEffectError(
                    "copy_values.activated_abilities must be an array"
                )
            state.activated_abilities = [
                value
                if isinstance(value, ActivatedAbility)
                else ActivatedAbility.from_dict(thaw_value(value))
                for value in replacement
            ]
        elif hasattr(state, key):
            setattr(state, key, replacement)
        else:
            raise ContinuousEffectError(
                f"Unknown copiable field {key!r}"
            )


def _apply_face_down(
    state: CharacteristicState,
    value: Any,
) -> None:
    values = dict(value or {})
    state.name = str(values.get("name") or "")
    state.mana_cost = str(values.get("mana_cost") or "")
    state.mana_value = float(values.get("mana_value") or 0)
    state.text = str(values.get("text") or "")
    state.executable_text = state.text
    state.supertypes = _as_words(values.get("supertypes"))
    state.card_types = _as_words(
        values.get("card_types", ["Creature"])
    )
    state.subtypes = _as_words(values.get("subtypes"))
    state.colors = _as_words(values.get("colors"))
    state.abilities = [
        str(item) for item in values.get("abilities", [])
    ]
    state.ability_fragments = []
    state.activated_abilities = []
    state.power = int(values.get("power", 2))
    state.toughness = int(values.get("toughness", 2))


def _apply_ability_operation(
    state: CharacteristicState,
    operation: ContinuousOperation,
) -> bool:
    op = operation.op
    value = operation.value
    if op == "add_ability":
        ability = str(value)
        if ability.casefold() not in {
            item.casefold() for item in state.abilities
        }:
            state.abilities.append(ability)
    elif op == "remove_ability":
        state.abilities = [
            ability
            for ability in state.abilities
            if ability.casefold() != str(value).casefold()
        ]
    elif op == "add_rules_text":
        line = str(value).strip()
        existing = [item.strip() for item in state.text.splitlines()]
        if line not in existing:
            state.text = "\n".join(
                item for item in (state.text.strip(), line) if item
            )
    elif op == "add_ability_fragment":
        fragment = ability_fragment_from_dict(value)
        state.ability_fragments = list(
            canonical_ability_fragments(
                (*state.ability_fragments, fragment)
            )
        )
    elif op == "remove_ability_fragment":
        fragment = ability_fragment_from_dict(value)
        state.ability_fragments = [
            candidate
            for candidate in state.ability_fragments
            if candidate != fragment
        ]
    elif op == "remove_all_abilities":
        state.abilities = []
        state.ability_fragments = []
        state.activated_abilities = []
    else:
        return False
    return True


def _apply_operation(
    state: CharacteristicState,
    operation: ContinuousOperation,
) -> None:
    op = operation.op
    value = operation.value
    if op == "copy_values":
        _apply_copy_values(state, value)
        return
    if op == "face_down":
        _apply_face_down(state, value)
        return
    if op == "set_controller":
        state.controller = str(value)
        return
    if op == "replace_text":
        if not isinstance(value, Mapping):
            raise ContinuousEffectError(
                "replace_text requires from/to"
            )
        state.text = state.text.replace(
            str(value.get("from") or ""),
            str(value.get("to") or ""),
        )
        state.executable_text = state.executable_text.replace(
            str(value.get("from") or ""),
            str(value.get("to") or ""),
        )
        # Text-changing effects can alter costs, activation restrictions, and
        # output. Until that grammar is compiled, do not retain a descriptor
        # for the pre-change text.
        state.activated_abilities = []
        return
    if op in {"set_types", "add_types", "remove_types"}:
        target = (
            "card_types"
            if operation.field in {None, "card_types"}
            else str(operation.field)
        )
        if target not in {"supertypes", "card_types", "subtypes"}:
            raise ContinuousEffectError(
                f"Invalid type field {target!r}"
            )
        values = _as_words(value)
        current = getattr(state, target)
        if op == "set_types":
            setattr(state, target, values)
        elif op == "add_types":
            current.update(values)
        else:
            current.difference_update(values)
        return
    if op in {"set_colors", "add_colors", "remove_colors"}:
        values = {item.upper() for item in _as_words(value)}
        if op == "set_colors":
            state.colors = values
        elif op == "add_colors":
            state.colors.update(values)
        else:
            state.colors.difference_update(values)
        return
    if op == "remove_all_colors":
        state.colors.clear()
        return
    if _apply_ability_operation(state, operation):
        return
    if op == "set_power_toughness":
        if not isinstance(value, Sequence) or len(value) != 2:
            raise ContinuousEffectError(
                "set_power_toughness requires [power, toughness]"
            )
        state.power = int(value[0])
        state.toughness = int(value[1])
        return
    if op == "modify_power_toughness":
        if not isinstance(value, Sequence) or len(value) != 2:
            raise ContinuousEffectError(
                "modify_power_toughness requires [power, toughness]"
            )
        if state.power is not None:
            state.power += int(value[0])
        if state.toughness is not None:
            state.toughness += int(value[1])
        return
    if op == "switch_power_toughness":
        state.power, state.toughness = state.toughness, state.power
        return
    raise ContinuousEffectError(
        f"Unsupported continuous operation {op!r}"
    )


def evaluate_continuous_effects(
    base: CharacteristicState,
    effects: Iterable[ContinuousEffect],
    *,
    context: Mapping[str, Any] | None = None,
) -> ContinuousEvaluation:
    context = dict(context or {})
    present: list[ContinuousEffect] = []
    inapplicable: list[str] = []
    for effect in effects:
        if (
            effect.duration
            is ContinuousEffectDuration.WHILE_SOURCE_PRESENT
            and not effect.source_present
        ):
            inapplicable.append(effect.effect_id)
            continue
        present.append(effect)
    ordered, cycles = order_continuous_effects(present)
    applied: list[str] = []
    for effect in ordered:
        if effect.relation in {
            ContinuousEffectRelation.SOURCE_OBJECT,
            ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,
        }:
            object_id = str(context.get("object_id") or "")
            logical_object_id = str(
                context.get("logical_object_id") or ""
            )
            if (
                effect.related_object is None
                or object_id != effect.related_object.object_id
                or logical_object_id
                != effect.related_object.logical_object_id
            ):
                inapplicable.append(effect.effect_id)
                continue
        if effect.locked_objects:
            object_id = str(context.get("object_id") or "")
            logical_object_id = str(
                context.get("logical_object_id") or ""
            )
            if not object_id or not logical_object_id:
                inapplicable.append(effect.effect_id)
                continue
            identity = ContinuousObjectIdentity(
                object_id=object_id,
                logical_object_id=logical_object_id,
            )
            if identity not in effect.locked_objects:
                inapplicable.append(effect.effect_id)
                continue
        # Applicability is evaluated against the characteristics produced by
        # earlier layers, not the object's unmodified starting values.
        if not _matches(effect.applies, base, context):
            inapplicable.append(effect.effect_id)
            continue
        for operation in effect.operations:
            _apply_operation(base, operation)
        if effect.layer == Layer.COPY and effect.sublayer == "1a":
            base.copiable_values = base.snapshot()
        applied.append(effect.effect_id)
    return ContinuousEvaluation(
        characteristics=base.snapshot(),
        applied_effects=tuple(applied),
        dependency_cycles=tuple(cycles),
        inapplicable_effects=tuple(inapplicable),
    )
